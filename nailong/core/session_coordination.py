"""Serial, durable dispatch into existing chats using the native execution path."""
from __future__ import annotations
import copy,hashlib,json,re,uuid
from contextvars import ContextVar
from pathlib import Path
from nailong.core.preferences import atomic_json,read_config
from nailong.core.file_locks import file_lock
from nailong.core.task_requests import task_request_kind

active_dispatch=ContextVar('nailong_dispatch',default=None)
TERMINAL={'completed','failed','cancelled','needs_reconciliation'}


def checkpoint(factory,thread):
    saved=factory.checkpointer.get_tuple({'configurable':{'thread_id':thread}})
    if saved is None:return None
    if any(row[1] in {'__interrupt__','__error__'} for row in (saved.pending_writes or [])):
        raise ValueError('目标存在未收束审批或异常 checkpoint。')
    messages=saved.checkpoint.get('channel_values',{}).get('messages',[])
    pending=set()
    for message in messages:
        if getattr(message,'type',None)=='ai':pending.update(call['id'] for call in getattr(message,'tool_calls',[]))
        elif getattr(message,'type',None)=='tool':pending.discard(message.tool_call_id)
    if pending:raise ValueError('目标存在结果未知的工具调用，不能自动接续。')
    return str(saved.config.get('configurable',{}).get('checkpoint_id') or '') or None


class SessionCoordination:
    def __init__(self,factory,owner):
        self.factory,self.owner=factory,owner
        self.sessions=factory.session_store;self.tasks=factory.task_store;self.goals=factory.goal_store
        self.path=self.sessions.root/'coordination.json';self.lock=self.sessions.root/'coordination.lock'

    def _read(self):
        data=read_config(self.path,self.sessions.root)
        if not data:return {'version':1,'project':str(self.sessions.project_root),'leases':{},'dispatches':{}}
        if data.get('version')!=1 or data.get('project')!=str(self.sessions.project_root) or not all(isinstance(data.get(key),dict) for key in ('leases','dispatches')):
            raise ValueError('协调控制记录损坏或属于其他项目。')
        for key,row in data['dispatches'].items():
            if not isinstance(row,dict) or row.get('request_id')!=key or row.get('state') not in {'queued','admitted','running',*TERMINAL}:
                raise ValueError('派发记录无效，拒绝恢复执行。')
        return data

    def _write(self,data):atomic_json(self.path,data,self.sessions.root)

    def summaries(self,query: str = '',offset: int = 0,limit: int = 5) -> dict:
        """有界列出当前项目会话与任务摘要，不加载全部历史。"""
        try:
            if not isinstance(query,str) or len(query)>1000 or type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=10:raise ValueError('分页无效。')
            rows=[]
            for session in self.sessions.list_sessions():
                if query and query.casefold() not in json.dumps(session,ensure_ascii=False).casefold():continue
                task=self.tasks.snapshot(session['thread_id'])
                rows.append({**session,'task':{key:task[key] for key in ('task_id','revision','objective','lifecycle','phase','scope')} if task else None})
            return {'ok':True,'sessions':rows[offset:offset+limit],'next_offset':offset+limit if offset+limit<len(rows) else None}
        except (ValueError,OSError,RuntimeError) as error:return {'ok':False,'error':str(error)}

    def history(self,thread_id: str,offset: int = 0,max_chars: int = 4000) -> dict:
        """定向读取当前项目会话事件，历史仅为资料，不授权接管。offset 为字符偏移。"""
        try:
            if type(offset) is not int or offset<0 or type(max_chars) is not int or not 1<=max_chars<=6000:raise ValueError('分页无效。')
            text=json.dumps(self.sessions.read_events(thread_id),ensure_ascii=False)
            key=getattr(self.factory.settings,'api_key','')
            if key:text=text.replace(key,'[密钥已隐藏]')
            return {'ok':True,'text':text[offset:offset+max_chars],'next_offset':offset+max_chars if offset+max_chars<len(text) else None,'historical':True}
        except (ValueError,OSError,RuntimeError) as error:return {'ok':False,'error':str(error)}

    def _goal(self):
        goal=self.goals.active()
        if goal is None or goal.thread_id!=self.owner:raise ValueError('接管需要当前会话的持续目标。')
        from nailong.core.task_intake import user_request
        request=user_request.get()
        # A live instruction or the explicit persistent goal may authorize coordination.
        texts=[goal.objective]
        if request and request.get('thread_id')==self.owner:texts.append(request.get('text',''))
        task=self.tasks.snapshot(self.owner)
        if task:texts.extend(task.get('request_history',[]))
        if not any(re.search(r'接管|协调|调度.*会话|协同|coordinate|take.over',text,re.I) and not re.search(r'不要|禁止|仅讨论|只讨论',text) for text in texts):
            raise ValueError('用户尚未要求接管或协调其他会话。')
        return goal

    def acquire(self,thread_id: str,expected_revision: int,scope: list[str]) -> dict:
        """为已授权协调目标接管一个现有会话；不改变其任务身份，不解除用户暂停。"""
        try:
            self.sessions.session_path(thread_id);goal=self._goal()
            if thread_id==self.owner:raise ValueError('当前会话无需派发给自己。')
            task=self.tasks.snapshot(thread_id)
            if task is None or task['revision']!=expected_revision or task['lifecycle']!='active':raise ValueError('目标任务版本变化、不存在或已暂停。')
            version=checkpoint(self.factory,thread_id)
            if not version:raise ValueError('目标缺少可接续的原生 checkpoint；不能从事件文字重建执行。')
            if not isinstance(scope,list) or not scope or len(scope)>20:raise ValueError('接管需明确范围。')
            scope=[self.tasks._relative(path) for path in scope]
            if any(not any(_inside(path,allowed) for allowed in task['scope']) for path in scope):raise ValueError('接管不能扩大原任务范围。')
            with file_lock(self.lock):
                data=self._read();previous=data['leases'].get(thread_id)
                if previous and previous['state']=='owned' and previous['owner']!=self.owner:raise ValueError('此会话已被其他协调来源控制。')
                if any(row['target']==thread_id and row['state'] in {'admitted','running'} for row in data['dispatches'].values()):raise ValueError('目标仍在执行，不能替换控制来源。')
                generation=previous['generation']+1 if previous else 1
                data['leases'][thread_id]={'owner':self.owner,'goal_id':goal.id,'generation':generation,'state':'owned','scope':scope,
                    'task_id':task['task_id'],'revision':task['revision'],'checkpoint':version}
                self._write(data)
            return {'ok':True,'generation':generation,'task_id':task['task_id'],'revision':task['revision']}
        except (ValueError,OSError,RuntimeError,TypeError) as error:return {'ok':False,'error':str(error)}

    def dispatch(self,request_id: str,thread_id: str,stage: str,generation: int,dependencies: list[str]) -> dict:
        """仅提交串行派发；request_id 幂等，依赖只能引用已提交请求，禁止循环。"""
        try:
            goal=self._goal()
            if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',request_id) or not isinstance(stage,str) or not stage.strip() or len(stage)>4000:
                raise ValueError('请求身份或阶段目标无效。')
            if type(generation) is not int or not isinstance(dependencies,list) or len(dependencies)>20 or any(not isinstance(row,str) for row in dependencies):raise ValueError('依赖或控制版本无效。')
            with file_lock(self.lock):
                data=self._read();lease=data['leases'].get(thread_id)
                if not lease or lease['owner']!=self.owner or lease['goal_id']!=goal.id or lease['generation']!=generation or lease['state']!='owned':raise ValueError('没有当前有效的接管控制权。')
                candidate={'request_id':request_id,'owner':self.owner,'goal_id':goal.id,'target':thread_id,'stage':stage,'generation':generation,
                    'task_id':lease['task_id'],'revision':lease['revision'],'scope':lease['scope'],'checkpoint':lease['checkpoint'],'dependencies':dependencies}
                previous=data['dispatches'].get(request_id)
                if previous:
                    if any(previous.get(key)!=value for key,value in candidate.items()):raise ValueError('同一 request_id 对应不同派发参数。')
                    return {'ok':True,'request_id':request_id,'state':previous['state'],'duplicate':True}
                if request_id in dependencies or any(dep not in data['dispatches'] or data['dispatches'][dep]['owner']!=self.owner or data['dispatches'][dep]['goal_id']!=goal.id for dep in dependencies):raise ValueError('依赖缺失、循环或不属于当前目标。')
                if len(data['dispatches'])>=200:raise ValueError('派发记录已达 200 条，请归档后继续。')
                data['dispatches'][request_id]={**candidate,'state':'queued','run_id':None,'result':None}
                self._write(data)
            return {'ok':True,'request_id':request_id,'state':'queued'}
        except (ValueError,OSError,RuntimeError,TypeError) as error:return {'ok':False,'error':str(error)}

    def status(self,request_id: str = '') -> dict:
        """查看当前协调来源的状态、结果及原生证据引用。"""
        try:
            with file_lock(self.lock):
                data=self._read()
                rows=[copy.deepcopy(row) for row in data['dispatches'].values() if row['owner']==self.owner and (not request_id or row['request_id']==request_id)]
            return {'ok':True,'dispatches':rows[:20],'truncated':len(rows)>20}
        except (ValueError,OSError,RuntimeError) as error:return {'ok':False,'error':str(error)}

    def pause(self,thread_id: str = '',release: bool = False) -> dict:
        """暂停或释放已接管会话；空 thread_id 暂停此来源的所有派发。"""
        try:
            with file_lock(self.lock):
                data=self._read()
                for thread,lease in data['leases'].items():
                    if lease['owner']!=self.owner or thread_id and thread!=thread_id:continue
                    lease['generation']+=1;lease['state']='released' if release else 'paused'
                    for row in data['dispatches'].values():
                        if row['target']==thread and row['state'] not in TERMINAL:
                            row['state']='cancelled' if row['state']=='queued' else 'needs_reconciliation'
                self._write(data)
            return {'ok':True}
        except (ValueError,OSError,RuntimeError) as error:return {'ok':False,'error':str(error)}

    def guard(self,row,*,check_checkpoint=False):
        with file_lock(self.lock):
            data=self._read();current=data['dispatches'].get(row['request_id']);lease=data['leases'].get(row['target'])
            if not current or current['state'] not in {'queued','admitted','running'} or not lease or lease['generation']!=row['generation'] or lease['state']!='owned' or lease['owner']!=row['owner']:
                raise ValueError('派发控制版本失效，停止后续操作。')
        task=self.tasks.snapshot(row['target']);goal=self.goals.get(row['goal_id'])
        if task is None or (task['task_id'],task['revision'])!=(row['task_id'],row['revision']) or task['lifecycle']!='active':raise ValueError('派发任务版本变化或已暂停。')
        if not goal or goal.state!='active' or goal.thread_id!=row['owner']:raise ValueError('协调目标已暂停或控制来源变化。')
        if check_checkpoint and checkpoint(self.factory,row['target'])!=row['checkpoint']:raise ValueError('原生 checkpoint 已变化，需重新接管核对。')
        return task

    def _transition(self,row,state,result=None):
        with file_lock(self.lock):
            data=self._read();saved=data['dispatches'][row['request_id']]
            if saved['run_id']!=row['run_id']:raise ValueError('原生派发身份变化。')
            if saved['state'] in TERMINAL:
                return saved['state']==state
            saved.update(state=state)
            if result is not None:saved['result']=result
            self._write(data)
        return True

    async def drain(self,service,*,approval=None,cost_budget=None):
        from agent_service import TurnEvent
        from nailong.core.budgets import GoalCostBudget
        from nailong.core.costs import CostEstimator
        from nailong.core.verification import VerificationService
        from nailong.core.project_execution import project_execution
        # Same-task reentrancy supports GUI's outer execution lease. No tool waits here.
        with project_execution(self.sessions):
            for _ in range(20):
                with file_lock(self.lock):
                    data=self._read()
                    rows=[row for row in data['dispatches'].values() if row['owner']==self.owner and row['state']=='queued'
                        and all(data['dispatches'][dep]['state']=='completed' for dep in row['dependencies'])]
                    if not rows:return
                    row=copy.deepcopy(rows[0])
                try:self.guard(row,check_checkpoint=True)
                except (ValueError,OSError,RuntimeError) as error:
                    row['run_id']=None;self._transition(row,'cancelled',{'reason':str(error)})
                    yield TurnEvent('notice',{'text':str(error),'request_id':row['request_id']});continue
                goal=self.goals.get(row['goal_id']);estimator=CostEstimator(self.factory.settings.model,self.factory.settings.project_root)
                budget=cost_budget or GoalCostBudget(estimator,goal.max_cost_usd-goal.spent_usd)
                row['run_id']=uuid.uuid4().hex
                with file_lock(self.lock):
                    data=self._read();data['dispatches'][row['request_id']].update(state='admitted',run_id=row['run_id']);self._write(data)
                self.guard(row);self._transition(row,'running')
                token=active_dispatch.set((self,row));error=None;delivery=None;complete=False;tool_failed=False
                before_spent=budget.spent
                try:
                    stream=service.stream_turn('协调阶段目标（不修改原任务要求）：'+row['stage'],{'configurable':{'thread_id':row['target']}},
                        pin_message='coordination',allowed_tools={'list_files','read_file','glob','grep','search_text','read_tool_result','write_file','edit_file','run_command'},
                        approval_handler=approval,cost_budget=budget)
                    try:
                        async for event in stream:
                            if event.kind=='usage':
                                event=service._event(self.owner,'usage',{**event.data,'scope':'coordination','dispatch_id':row['request_id'],'run_id':row['run_id'],'worker_thread':row['target']})
                            if event.kind=='tool_end' and not event.data.get('ok'):tool_failed=True
                            if event.kind=='final':
                                complete=True;delivery=event.data.get('delivery')
                                yield TurnEvent('coordination_update',{'request_id':row['request_id'],'text':event.data.get('text','')})
                            elif event.kind=='token':
                                yield TurnEvent('coordination_update',{'request_id':row['request_id'],'text':event.data.get('text','')})
                            else:yield event
                    finally:await stream.aclose()
                    self.guard(row)
                    if row['scope']==['.']:
                        verification=VerificationService(self.factory.settings.project_root,self.sessions,self.goals,
                            task_store=self.tasks,api_key=self.factory.settings.api_key)
                        if verification.list_steps():await verification.run(row['target'],approval=approval,permission_engine=service.permission_engine,permission_mode=service.permission_mode)
                    delivery=await service.delivery_report(row['target'])
                except BaseException as caught:error=caught
                finally:
                    active_dispatch.reset(token)
                    outcome='needs_reconciliation' if error else 'completed' if complete and budget.usage_complete and not tool_failed else 'failed'
                    result={'run_id':row['run_id'],'delivery':delivery,'turn_completed':complete,'usage_complete':budget.usage_complete,
                        'event_reference':'events:'+row['target'],'error':type(error).__name__ if error else None}
                    self._transition(row,outcome,result)
                    if cost_budget is None:
                        self.goals.record_round(goal.id,cost_usd=max(0,budget.spent-before_spent),files_changed=False,tool_calls=0,
                            summary='协调派发 '+row['request_id'],round_id='dispatch:'+row['run_id'],
                            stop_reason='协调执行中断或用量未知。' if error or not budget.usage_complete else None)
                yield service._event(self.owner,'notice',{'text':'会话 '+row['target']+' 派发状态：'+outcome+'；目标验收：'+str((delivery or {}).get('status','unverified'))})
                yield service._event(self.owner,'coordination_result',{'request_id':row['request_id'],'state':outcome,**result})
                if error:raise error


def _inside(path,scope):
    return scope=='.' or path==scope or path.startswith(scope.rstrip('/')+'/')


def is_native_coordination(spec,root):
    owner=getattr(getattr(spec,'handler',None),'__self__',None)
    return type(owner) is SessionCoordination and Path(owner.sessions.project_root).resolve()==root and getattr(spec.handler,'__func__',None) in {
        SessionCoordination.acquire,SessionCoordination.dispatch,SessionCoordination.pause}


def execution_guard(spec,args,thread_id):
    active=active_dispatch.get()
    if active is None:return None
    coordinator,row=active
    try:
        if thread_id!=row['target']:raise ValueError('派发工具属于其他会话。')
        coordinator.guard(row)
        if spec.name=='run_command' and row['scope']!=['.']:raise ValueError('命令无法保证只影响局部文件范围，当前接管不允许运行。')
        if not spec.read_only and spec.name not in {'write_file','edit_file','run_command'}:raise ValueError('派发不能修改控制权或创建嵌套任务。')
        path=args.get('path')
        if path is not None:
            path=coordinator.tasks._relative(path)
            if not any(_inside(path,scope) for scope in row['scope']):raise ValueError('工具路径超出本次接管范围。')
    except (ValueError,OSError,RuntimeError,KeyError,TypeError) as error:
        from nailong.tools.results import failure
        return failure('dispatch_invalidated',str(error),started=False)
    return None
