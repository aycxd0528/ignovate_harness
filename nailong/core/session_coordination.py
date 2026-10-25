"""Serial, durable dispatch into existing chats using the native execution path."""
from __future__ import annotations
import asyncio,copy,hashlib,json,re,uuid
from contextlib import ExitStack
from contextvars import ContextVar
from pathlib import Path
from nailong.core.preferences import atomic_json,read_config
from nailong.core.file_locks import file_lock
from nailong.core.task_requests import task_request_kind
from nailong.core.session_policy import MODES,session_mode,restricted_mode
from nailong.core.verification import input_fingerprint,_owned_worker

active_dispatch=ContextVar('nailong_dispatch',default=None)
TERMINAL={'completed','partial','failed','cancelled','needs_reconciliation'}
AUTHORITY_FIELDS=('owner','goal_id','generation','task_id','revision','scope','permission_mode')


def _request_id(value):
    return isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value) is not None


def _observed_tools(result):
    count=result.get('observed_tools',0) if isinstance(result,dict) else 0
    return count if type(count) is int and count>=0 else 0


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
        if not data:
            if self.path.exists():raise ValueError('协调控制记录为空或损坏。')
            return {'version':1,'project':str(self.sessions.project_root),'leases':{},'dispatches':{}}
        if type(data.get('version')) is not int or data['version']!=1 or data.get('project')!=str(self.sessions.project_root) or not all(isinstance(data.get(key),dict) for key in ('leases','dispatches')):
            raise ValueError('协调控制记录损坏或属于其他项目。')
        if len(data['dispatches'])>200:raise ValueError('派发记录超过 200 条，拒绝恢复执行。')
        for thread,row in data['leases'].items():
            self.sessions.session_path(thread);self._validate_authority(row)
            if not isinstance(row.get('state'),str) or row['state'] not in {'owned','paused','released'}:
                raise ValueError('接管控制状态无效，拒绝恢复执行。')
        for key,row in data['dispatches'].items():
            self._validate_authority(row)
            if (not _request_id(key) or row.get('request_id')!=key
                    or not isinstance(row.get('state'),str) or row['state'] not in {'queued','admitted','running',*TERMINAL}
                    or not isinstance(row.get('stage'),str) or not row['stage'].strip() or len(row['stage'])>4000
                    or not isinstance(row.get('dependencies'),list) or len(row['dependencies'])>20
                    or any(not _request_id(dependency) for dependency in row['dependencies'])
                    or 'run_id' not in row or row['run_id'] is not None and (not isinstance(row['run_id'],str) or not row['run_id'])
                    or row['state']=='queued' and row['run_id'] is not None
                    or row['state'] in {'admitted','running'} and row['run_id'] is None
                    or row.get('result') is not None and not isinstance(row['result'],dict)):
                raise ValueError('派发记录无效，拒绝恢复执行。')
            self.sessions.session_path(row.get('target'))
            lease=data['leases'].get(row['target'])
            if (not lease or row['target']==row['owner'] or row['generation']>lease['generation']
                    or row['generation']==lease['generation'] and any(row[field]!=lease[field] for field in AUTHORITY_FIELDS)):
                raise ValueError('派发记录与接管身份不一致，拒绝恢复执行。')
        for key,row in data['dispatches'].items():
            for dependency in row['dependencies']:
                previous=data['dispatches'].get(dependency)
                if dependency==key or not previous or any(previous[field]!=row[field] for field in ('owner','goal_id')):
                    raise ValueError('派发依赖缺失、循环或属于其他来源。')
        pending=set();visited=set()
        def visit(identity):
            if identity in pending:raise ValueError('派发依赖存在循环，拒绝恢复执行。')
            if identity in visited:return
            pending.add(identity)
            for dependency in data['dispatches'][identity]['dependencies']:visit(dependency)
            pending.remove(identity);visited.add(identity)
        for identity in data['dispatches']:visit(identity)
        return data

    def _validate_authority(self,row):
        if (not isinstance(row,dict)
                or any(not isinstance(row.get(field),str) or not row[field].strip() for field in ('goal_id','task_id','checkpoint'))
                or any(type(row.get(field)) is not int or row[field]<1 for field in ('generation','revision'))
                or not isinstance(row.get('scope'),list) or not 1<=len(row['scope'])<=20
                or not isinstance(row.get('permission_mode'),str) or row['permission_mode'] not in MODES):
            raise ValueError('协调控制身份、版本或范围无效。')
        self.sessions.session_path(row.get('owner'))
        for path in row['scope']:self.tasks._validate_recorded_path(path)

    def _write(self,data):
        if len(json.dumps(data,ensure_ascii=False).encode())>900000:raise ValueError('协调状态超过安全存储上限。')
        atomic_json(self.path,data,self.sessions.root)

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
            if type(expected_revision) is not int or expected_revision<1:raise ValueError('任务修订必须是正整数。')
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
                    'task_id':task['task_id'],'revision':task['revision'],'checkpoint':version,'permission_mode':session_mode(self.sessions,thread_id)}
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
            # Serialize admission with final goal completion. _goal() includes
            # task reads, so only recheck the durable goal inside this lock.
            with self.goals._locked(), file_lock(self.lock):
                current=self.goals._load().get(goal.id)
                if current != goal or current.state!='active' or current.thread_id!=self.owner:
                    raise ValueError('协调目标状态、要求或来源已变化，不能继续派发。')
                data=self._read();lease=data['leases'].get(thread_id)
                if not lease or lease['owner']!=self.owner or lease['goal_id']!=goal.id or lease['generation']!=generation or lease['state']!='owned':raise ValueError('没有当前有效的接管控制权。')
                candidate={'request_id':request_id,'owner':self.owner,'goal_id':goal.id,'target':thread_id,'stage':stage,'generation':generation,
                    'task_id':lease['task_id'],'revision':lease['revision'],'scope':lease['scope'],'checkpoint':lease['checkpoint'],
                    'permission_mode':lease['permission_mode'],'dependencies':dependencies}
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

    def intervene(self,thread_id,*,source_changed=False):
        """Only the outer real-user adapter calls this; no model tool."""
        with file_lock(self.lock):
            data=self._read()
            for thread,lease in data['leases'].items():
                if thread!=thread_id and not (source_changed and lease['owner']==thread_id):continue
                lease['generation']+=1;lease['state']='paused'
                for row in data['dispatches'].values():
                    if row['target']==thread and row['state'] not in TERMINAL:
                        row['state']='cancelled' if row['state']=='queued' else 'needs_reconciliation'
            self._write(data)

    def reconcile(self):
        """Recover proven receipts only; uncertain effects remain non-runnable."""
        steps=self._reconcile_steps()
        try:
            root=next(steps)
            while True:
                try:value=input_fingerprint(root)
                except (ValueError,OSError):value=None
                root=steps.send(value)
        except StopIteration:pass
        finally:steps.close()

    async def reconcile_async(self):
        """Keep state ownership local; only input observations run off-loop."""
        steps=self._reconcile_steps()
        try:
            root=next(steps)
            while True:
                try:value=await _owned_worker(input_fingerprint,root)
                except (ValueError,OSError):value=None
                root=steps.send(value)
        except StopIteration:pass
        finally:steps.close()

    def _reconcile_steps(self):
        with file_lock(self.lock):
            data=self._read()
            uncertain=[copy.deepcopy(row) for row in data['dispatches'].values()
                if row['owner']==self.owner and row['state'] in {'admitted','running'}]
        for row in uncertain:
            receipts=[event.get('data',{}) for event in self.sessions.read_events(row['target'])
                if event.get('kind')=='coordination_receipt' and event.get('data',{}).get('request_id')==row['request_id']
                and event.get('data',{}).get('run_id')==row['run_id']]
            receipt=receipts[-1] if receipts else None
            proven=False
            if receipt and all(receipt.get(key)==row[key] for key in ('task_id','revision','generation')) and receipt.get('state')=='completed' and receipt.get('usage_complete'):
                task=self.tasks.snapshot(row['target']);goal=self.goals.get(row['goal_id'])
                proven=bool(task and (task['task_id'],task['revision'])==(row['task_id'],row['revision']) and goal
                    and isinstance(receipt.get('checkpoint'),str) and receipt['checkpoint'].strip()
                    and checkpoint(self.factory,row['target'])==receipt.get('checkpoint'))
                if proven:
                    current=yield self.factory.settings.project_root
                    proven=bool(receipt.get('input_fingerprint') and current==receipt['input_fingerprint'])
                if proven:
                    try:
                        self.guard(row,allow_paused_goal=receipt['budget_round_id'] in goal.settled_round_ids)
                        proven=checkpoint(self.factory,row['target'])==receipt.get('checkpoint')
                    except (ValueError,OSError,RuntimeError):proven=False
                if proven and receipt['budget_round_id'] not in goal.settled_round_ids:
                    if receipt.get('standalone'):
                        self.goals.record_round(goal.id,cost_usd=receipt['cost_usd'],files_changed=False,tool_calls=_observed_tools(receipt.get('result')),
                            summary='恢复已确认派发回执',round_id=receipt['budget_round_id'])
                    else:proven=False  # Parent-round usage may also have been billed.
            self._transition(row,'completed' if proven else 'needs_reconciliation',
                receipt.get('result') if proven else {'reason':'原生执行结果或父轮费用结算未知；未重放。'})
            if not proven:
                self.goals.update(row['goal_id'],state='paused',summary='派发结果待核对，未重放未知操作。',thread_id=row['owner'])

    def guard(self,row,*,check_checkpoint=False,allow_paused_goal=False):
        with file_lock(self.lock):
            data=self._read();current=data['dispatches'].get(row['request_id']);lease=data['leases'].get(row['target'])
            if not current or current['state'] not in {'queued','admitted','running'} or not lease or lease['generation']!=row['generation'] or lease['state']!='owned' or lease['owner']!=row['owner']:
                raise ValueError('派发控制版本失效，停止后续操作。')
        if session_mode(self.sessions,row['target'])!=row['permission_mode']:raise ValueError('目标会话权限模式已变化，请重新核对控制权。')
        task=self.tasks.snapshot(row['target']);goal=self.goals.get(row['goal_id'])
        if task is None or (task['task_id'],task['revision'])!=(row['task_id'],row['revision']) or task['lifecycle']!='active':raise ValueError('派发任务版本变化或已暂停。')
        if not goal or goal.state not in ({'active','paused'} if allow_paused_goal else {'active'}) or goal.thread_id!=row['owner']:raise ValueError('协调目标已暂停或控制来源变化。')
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
            await self.reconcile_async()
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
                row['run_id']=uuid.uuid4().hex
                budget=cost_budget or GoalCostBudget(estimator,goal.max_cost_usd-goal.spent_usd,
                    goal_store=self.goals,goal_id=goal.id,round_id='dispatch:'+row['run_id'])
                if cost_budget is None:budget.goal_round_id='dispatch:'+row['run_id']
                with file_lock(self.lock):
                    data=self._read();data['dispatches'][row['request_id']].update(state='admitted',run_id=row['run_id']);self._write(data)
                self.guard(row);self._transition(row,'running')
                from nailong.core.permissions import PermissionEngine
                prior_mode,prior_engine=service.permission_mode,service.permission_engine
                prior_task=getattr(service,'last_turn_task_id',None)
                service.set_permission_mode(restricted_mode(prior_mode,row['permission_mode']))
                service.permission_engine=PermissionEngine(self.factory.settings.project_root)
                execution=getattr(self.factory,'tool_execution_context',None)
                if execution is not None:execution.permission_engine=service.permission_engine
                token=active_dispatch.set((self,row));error=None;delivery=None;complete=False;tool_failed=False;verification_status=None;observed_tools=0
                before_spent=budget.spent
                with ExitStack() as budget_cleanup:
                    if cost_budget is None:budget_cleanup.callback(budget.close)
                    try:
                        stream=service.stream_turn('协调阶段目标（不修改原任务要求）：'+row['stage'],{'configurable':{'thread_id':row['target']}},
                            pin_message='coordination',allowed_tools={'list_files','read_file','glob','grep','search_text','read_tool_result','write_file','edit_file','run_command'},
                            approval_handler=approval,cost_budget=budget)
                        try:
                            async for event in stream:
                                if event.kind in {'usage','usage_missing'}:
                                    event=service._event(self.owner,event.kind,{**event.data,'scope':'coordination','dispatch_id':row['request_id'],'run_id':row['run_id'],'worker_thread':row['target']})
                                if event.kind=='tool_end' and not event.data.get('ok'):tool_failed=True
                                if event.kind=='tool_end' and event.data.get('ok'):observed_tools+=1
                                if event.kind=='final':
                                    complete=True;delivery=event.data.get('delivery')
                                    yield TurnEvent('coordination_update',{'request_id':row['request_id'],'text':event.data.get('text','')})
                                elif event.kind=='token':
                                    yield TurnEvent('coordination_update',{'request_id':row['request_id'],'text':event.data.get('text','')})
                                else:yield event
                        finally:await stream.aclose()
                        self.guard(row)
                        target_task=self.tasks.snapshot(row['target'])
                        target_intake=(target_task or {}).get('intake',{})
                        if row['scope']==['.'] and (target_task or {}).get('profile','chat')=='chat':
                            verification=VerificationService(self.factory.settings.project_root,self.sessions,self.goals,
                                task_store=self.tasks,api_key=self.factory.settings.api_key,
                                steps_override=target_intake.get('verification') if target_intake.get('stage')=='executing' and target_intake.get('verification') else None)
                            if verification.list_steps():
                                verified=await verification.run(row['target'],approval=approval,permission_engine=service.permission_engine,permission_mode=service.permission_mode)
                                verification_status=verified['status']
                        delivery=await service.delivery_report(row['target'])
                        self.guard(row)
                    except BaseException as caught:error=caught
                    finally:
                        active_dispatch.reset(token)
                        service.set_permission_mode(prior_mode);service.permission_engine=prior_engine
                        service.last_turn_task_id=prior_task
                        if execution is not None:execution.permission_engine=prior_engine
                        outcome='needs_reconciliation' if error else 'completed' if complete and budget.usage_complete and not tool_failed else 'failed'
                        if outcome=='completed':
                            if verification_status in {'failed','invalidated','cancelled'} or (delivery or {}).get('status')=='failed':outcome='failed'
                            elif verification_status in {'partial','unverified'} or any((delivery or {}).get(key) for key in ('pending','stale')):outcome='partial'
                            elif not observed_tools and verification_status!='passed' and (delivery or {}).get('status') not in {'verified','reviewed','user_confirmed'}:outcome='partial'
                        receipt_fingerprint=None
                        if error is None:
                            try:receipt_fingerprint=await _owned_worker(input_fingerprint,self.factory.settings.project_root)
                            except asyncio.CancelledError as caught:
                                error=caught;outcome='needs_reconciliation'
                            except (ValueError,OSError):pass
                        if outcome=='completed' and not receipt_fingerprint:outcome='needs_reconciliation'
                        receipt_checkpoint=None
                        if error is None:
                            try:
                                self.guard(row)
                                receipt_checkpoint=checkpoint(self.factory,row['target'])
                                if not isinstance(receipt_checkpoint,str) or not receipt_checkpoint.strip():
                                    raise ValueError('最终原生 checkpoint 缺失，派发结果待核对。')
                            except (ValueError,OSError,RuntimeError) as caught:
                                error=caught;outcome='needs_reconciliation'
                        result={'run_id':row['run_id'],'delivery':delivery,'turn_completed':complete,'usage_complete':budget.usage_complete,
                            'verification_status':verification_status,'observed_tools':observed_tools,'event_reference':'events:'+row['target'],'error':type(error).__name__ if error else None}
                        receipt={'request_id':row['request_id'],'run_id':row['run_id'],'task_id':row['task_id'],
                            'revision':row['revision'],'generation':row['generation'],'state':outcome,'result':result,
                            'usage_complete':budget.usage_complete,'budget_round_id':getattr(budget,'goal_round_id','unknown'),
                            'standalone':cost_budget is None,'cost_usd':max(0,budget.spent-before_spent),
                            'input_fingerprint':receipt_fingerprint,
                            'checkpoint':receipt_checkpoint}
                        self.sessions.append_event(row['target'],'coordination_receipt',receipt)
                        if cost_budget is None:
                            self.goals.record_round(goal.id,cost_usd=receipt['cost_usd'],files_changed=False,tool_calls=observed_tools,
                                summary='协调派发 '+row['request_id'],round_id=receipt['budget_round_id'],
                                stop_reason='协调执行中断或用量未知。' if error and not isinstance(error,ValueError) or not budget.usage_complete else None)
                        self._transition(row,outcome,result)
                        if outcome=='completed':
                            with file_lock(self.lock):
                                data=self._read();lease=data['leases'][row['target']]
                                if lease['generation']==row['generation'] and lease['state']=='owned':
                                    lease['checkpoint']=receipt['checkpoint']
                                    for waiting in data['dispatches'].values():
                                        if waiting['target']==row['target'] and waiting['generation']==row['generation'] and waiting['state']=='queued' and waiting['checkpoint']==row['checkpoint']:
                                            waiting['checkpoint']=receipt['checkpoint']
                                self._write(data)
                    if isinstance(error,(asyncio.CancelledError,GeneratorExit)):raise error
                    yield service._event(self.owner,'notice',{'text':'会话 '+row['target']+' 派发状态：'+outcome+'；目标验收：'+str((delivery or {}).get('status','unverified'))})
                    yield service._event(self.owner,'coordination_result',{'request_id':row['request_id'],'state':outcome,**result})
                    if error:
                        if isinstance(error,ValueError):
                            yield service._event(self.owner,'notice',{'text':str(error)})
                        else:raise error


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
        task=coordinator.guard(row)
        if task.get('profile','chat')!='chat' and not spec.read_only:raise ValueError('目标原有模式仅允许调研，派发不能扩大执行模式。')
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
