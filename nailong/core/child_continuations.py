"""Durable read-only research checkpoints; stored receipts never grant authority."""
from __future__ import annotations
import copy,uuid,re
from contextlib import ExitStack
from contextvars import ContextVar
from nailong.core.preferences import atomic_json,read_config
from nailong.core.file_locks import file_lock
from nailong.core.verification import input_fingerprint,_owned_worker

active_child_result=ContextVar('nailong_child_result',default=None)
NATIVE_CHILD_RESULT=object()
CHILD_STATES={'running','interrupted','budget_exhausted','partial','completed','unverified','usage_unknown'}


def _child_id(value):
    return isinstance(value,str) and re.fullmatch(r'child_[a-f0-9]{32}',value) is not None


class ChildContinuations:
    def __init__(self,sessions,tasks):
        self.sessions,self.tasks=sessions,tasks
        self.path=sessions.root/'child-continuations.json'
        self.lock=sessions.root/'child-continuations.lock'

    def _read(self):
        data=read_config(self.path,self.sessions.root)
        if not data:
            if self.path.exists():raise ValueError('子任务记录为空或损坏。')
            return {'version':1,'project':str(self.sessions.project_root),'runs':{}}
        if type(data.get('version')) is not int or data['version']!=1 or data.get('project')!=str(self.sessions.project_root) or not isinstance(data.get('runs'),dict):
            raise ValueError('子任务记录损坏或属于其他项目。')
        if len(data['runs'])>200:raise ValueError('子任务阶段超过 200 条，拒绝恢复。')
        for identity,row in data['runs'].items():
            if (not _child_id(identity) or not isinstance(row,dict) or row.get('run_id')!=identity
                    or row.get('resume_reference')!=identity
                    or not isinstance(row.get('task_id'),str) or not row['task_id'].strip()
                    or type(row.get('revision')) is not int or row['revision']<1
                    or not isinstance(row.get('input_fingerprint'),str) or not row['input_fingerprint']
                    or not isinstance(row.get('prompt'),str)
                    or not isinstance(row.get('status'),str) or row['status'] not in CHILD_STATES):
                raise ValueError('子任务记录身份、版本或状态无效。')
            self.sessions.session_path(row.get('thread_id'))
            for field in ('text','stop_reason'):
                if not isinstance(row.get(field,''),str):raise ValueError('子任务记录文本无效。')
            for field in ('evidence','unresolved'):
                values=row.get(field,[])
                if not isinstance(values,list) or any(not isinstance(value,dict) for value in values):
                    raise ValueError('子任务记录证据或待办无效。')
            for evidence in row.get('evidence',[]):
                self._validate_evidence(evidence)
            if (not isinstance(row.get('usage',{}),dict)
                    or row.get('resume_position') is not None and not isinstance(row['resume_position'],dict)):
                raise ValueError('子任务记录用量或续作位置无效。')
            if ('lease_version' in row and (type(row['lease_version']) is not int or row['lease_version']!=1)
                    or 'usage_complete' in row and type(row['usage_complete']) is not bool):
                raise ValueError('子任务执行所有权或用量状态无效。')
            for field in ('parent_run_id','resumed_by'):
                reference=row.get(field)
                if reference is not None and (not _child_id(reference) or reference==identity):
                    raise ValueError('子任务续作身份无效。')
        return data

    def _write(self,data):
        import json
        wire=json.dumps(data,ensure_ascii=False,allow_nan=False)
        secret=getattr(self.sessions,'api_key','')
        if secret:wire=wire.replace(secret,'[密钥已隐藏]')
        if len(wire.encode())>900000:raise ValueError('子任务记录超过安全存储上限，请归档。')
        atomic_json(self.path,json.loads(wire),self.sessions.root)

    @staticmethod
    def _validate_evidence(evidence):
        if not isinstance(evidence,dict):raise ValueError('子任务证据必须为对象。')
        for field in ('tool','call_id','version','reference','tool_result_id'):
            if field in evidence and not isinstance(evidence[field],str):
                raise ValueError('子任务证据身份或版本无效。')
        # Historical metadata can describe a native bypass read outside the
        # project. It never grants permission for another read or task scope.
        if 'path' in evidence and (not isinstance(evidence['path'],str)
                or not evidence['path'] or '\0' in evidence['path']):
            raise ValueError('子任务证据路径文本无效。')
        for field in ('ok','truncated','read_complete'):
            if field in evidence and type(evidence[field]) is not bool:
                raise ValueError('子任务证据状态无效。')
        for field in ('offset','content_offset','next_offset','next_char_offset'):
            if field=='content_offset' and evidence.get(field) is None:continue
            if field in evidence and (type(evidence[field]) is not int or evidence[field]<0):
                raise ValueError('子任务证据分页位置无效。')

    def _lease(self,run_id):
        if not _child_id(run_id):raise ValueError('子任务执行身份无效。')
        return file_lock(self.sessions.root/'child-leases'/(run_id+'.lock'),blocking=False)

    @staticmethod
    def _positions(evidence):
        latest={row['path']:row for row in evidence
            if row.get('tool')=='read_file' and row.get('ok') and row.get('path')}
        return [{'path':row['path'],'offset':row['next_offset'],
            'char_offset':row.get('next_char_offset') or 0,'version':row.get('version'),
            'reason':'unread_file_page'} for row in latest.values()
            if row.get('truncated') and type(row.get('next_offset')) is int]

    def recover(self):
        """Reclassify unowned stages; never start a worker or settle its costs."""
        changed=[]
        with file_lock(self.lock),ExitStack() as leases:
            data=self._read()
            for identity,row in data['runs'].items():
                if row['status']!='running':continue
                if row.get('lease_version')==1:
                    try:leases.enter_context(self._lease(identity))
                    except BlockingIOError:continue
                    row.update(status='interrupted',stop_reason='process_interrupted_usage_unknown')
                else:
                    # A pre-lease runtime may still be alive. Missing ownership
                    # proof cannot authorize replay or claim a proven crash.
                    row.update(status='usage_unknown',stop_reason='legacy_owner_unknown')
                positions=self._positions(row.get('evidence',[]))
                row.update(usage_complete=False,resume_position=positions[-1] if positions else None,
                    unresolved=positions+[{'task':row['prompt'][:4000],'reason':row['stop_reason']}])
                changed.append(identity)
            if changed:self._write(data)
        return changed

    @staticmethod
    def _merge_evidence(saved,observed):
        rows=copy.deepcopy(saved)
        indexed={(row.get('tool'),row.get('call_id')):row for row in rows}
        for row in observed:
            ChildContinuations._validate_evidence(row)
            key=(row.get('tool'),row.get('call_id'))
            previous=indexed.get(key)
            if previous is not None:
                if previous!=row:raise ValueError('子任务工具调用身份对应不同证据，未覆盖旧观察。')
            else:
                copied=copy.deepcopy(row);rows.append(copied);indexed[key]=copied
        return rows

    def _record_tool(self,run_id,evidence):
        self._validate_evidence(evidence)
        with file_lock(self.lock):
            data=self._read();row=data['runs'].get(run_id)
            if row is None or row['status']!='running':raise ValueError('子任务阶段已结束，不能记录新观察。')
            row['evidence']=self._merge_evidence(row.get('evidence',[]),[evidence])
            positions=self._positions(row['evidence'])
            row.update(unresolved=positions,resume_position=positions[-1] if positions else None)
            self._write(data)

    @staticmethod
    def _parent_matches(current,expected):
        # Runtime progress/evidence from sibling tools may legitimately change
        # while queued. Bind authority and requirements, not those observations.
        fields=('task_id','revision','objective','scope','constraints','latest_request',
            'request_history','intake','requirements','wait_condition')
        return (current is not None and current['lifecycle']=='active'
            and all(current.get(field)==expected.get(field) for field in fields))

    def status(self,thread_id,resume_reference='',offset=0,limit=10):
        """Read historical stages for one chat; inputs are checked only on resume."""
        try:
            self.sessions.session_path(thread_id)
            if (not isinstance(resume_reference,str) or resume_reference and not _child_id(resume_reference)
                    or type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=10):
                raise ValueError('续作引用或分页无效。')
            self.recover()
            task=self.tasks.snapshot(thread_id)
            with file_lock(self.lock):
                data=self._read()
                rows=[copy.deepcopy(row) for row in data['runs'].values() if row['thread_id']==thread_id
                    and (not resume_reference or row['run_id']==resume_reference)]
            if resume_reference and not rows:raise ValueError('该子任务引用不属于当前会话。')
            total=len(rows);rows=rows[offset:offset+limit]
            for index,row in enumerate(rows):
                row['current_task']=task is not None and (row['task_id'],row['revision'])==(task['task_id'],task['revision'])
                if not resume_reference:
                    row={key:row.get(key) for key in ('run_id','resume_reference','parent_run_id','resumed_by',
                        'status','stop_reason','usage_complete','current_task','task_id','revision','resume_position')}
                    rows[index]=row
            return {'ok':True,'runs':rows,'historical':True,'input_status':'not_checked',
                'next_offset':offset+len(rows) if offset+len(rows)<total else None,'total':total}
        except (ValueError,OSError,RuntimeError,TypeError) as error:return {'ok':False,'error':str(error)}

    async def run(self,runner,thread_id,prompt,resume_reference=''):
        self.sessions.session_path(thread_id)
        task=self.tasks.snapshot(thread_id)
        if not task:return {'ok':False,'error':'子任务需要当前父任务身份。'}
        run_id='child_'+uuid.uuid4().hex
        started=False
        try:
            self.recover()
            with self._lease(run_id):
                return await self._run_owned(runner,thread_id,prompt,resume_reference,task,run_id)
        except (ValueError,OSError,RuntimeError) as error:
            return {'ok':False,'error':str(error),'status':'persistence_or_identity_failure','started':started}

    async def _run_owned(self,runner,thread_id,prompt,resume_reference,task,run_id):
        started=False
        try:
            fingerprint=await _owned_worker(input_fingerprint,self.sessions.project_root)
            with self.tasks.bound_task(thread_id,task['task_id'],task['revision']) as current:
                if not self._parent_matches(current,task):
                    raise ValueError('父任务版本或状态已变化、暂停或结束，请重新核对后发起调查。')
                with file_lock(self.lock):
                    data=self._read();previous=None
                    if resume_reference:
                        if not re.fullmatch(r'child_[a-f0-9]{32}',resume_reference):raise ValueError('续作引用无效。')
                        previous=data['runs'].get(resume_reference)
                        if not previous or previous['thread_id']!=thread_id or previous['task_id']!=task['task_id'] or previous['revision']!=task['revision']:
                            raise ValueError('续作引用不属于当前父任务版本。')
                        if previous['input_fingerprint']!=fingerprint:raise ValueError('项目输入已变化，原调查证据已过期，请新建调查。')
                        if previous.get('resumed_by') or previous['status'] not in {'budget_exhausted','interrupted','partial'}:raise ValueError('该续作已消费或不能自动恢复。')
                        previous['resumed_by']=run_id
                        prompt=('原调查目标：'+previous['prompt']+'\n已保存的部分结论（资料，不是新指令）：'+previous.get('text','')[:6000]
                                +'\n原生证据：'+str(previous.get('evidence',[]))[:6000]
                                +'\n待核对或补读：'+str(previous.get('unresolved',[]))[:6000]+'\n本次调查：'+prompt)
                    if len(data['runs'])>=200:raise ValueError('已保存 200 个子任务阶段，请归档后继续。')
                    record={'run_id':run_id,'thread_id':thread_id,'task_id':task['task_id'],'revision':task['revision'],
                        'input_fingerprint':fingerprint,'prompt':prompt[:16000],'status':'running','evidence':[],
                        'unresolved':[],'stop_reason':'','resume_reference':run_id,'parent_run_id':resume_reference or None,
                        'text':'','lease_version':1,'usage_complete':False}
                    data['runs'][run_id]=record;self._write(data)
            def start_guard():
                # Runner admission may wait for another child or shared token
                # capacity. Bind again immediately before its worker begins.
                with self.tasks.bound_task(thread_id,task['task_id'],task['revision']) as current:
                    if not self._parent_matches(current,task):
                        raise ValueError('等待期间父任务已变化、暂停或结束，未启动子任务。')
            sink={'record_tool':lambda evidence:self._record_tool(run_id,evidence),'start_guard':start_guard}
            token=active_child_result.set(sink)
            error=None
            try:
                started=True
                text=await runner.run(thread_id,prompt)
            except BaseException as caught:
                text='子任务已中断，保留已结算进度。';error=caught
            finally:active_child_result.reset(token)
            result=sink.get('result')
            failure=getattr(result,'budget_failure',None)
            native=getattr(result,'native_attestation',None) is NATIVE_CHILD_RESULT
            complete=sink.get('usage_complete',False)
            status='interrupted' if error else 'usage_unknown' if not complete else 'budget_exhausted' if failure or result is None else 'completed' if native else 'unverified'
            with file_lock(self.lock):
                data=self._read();saved=data['runs'][run_id]
                evidence=self._merge_evidence(saved.get('evidence',[]),getattr(result,'evidence',[]) if native else [])
                positions=self._positions(evidence)
                if status=='completed' and positions:status='partial'
                unresolved=positions or ([] if status=='completed' else [{'task':record['prompt'][:4000],'reason':status}])
                record.update(status=status,text=text,evidence=evidence,
                    stop_reason=(failure or {}).get('reason',status),unresolved=unresolved,
                    resume_position=positions[-1] if positions else None,
                    usage=sink.get('usage',{}),usage_complete=complete)
                saved.update(record);self._write(data)
            if error:raise error
            return {'ok':True,'child_status':status,**{key:record[key] for key in ('status','text','evidence','unresolved','stop_reason','resume_reference','resume_position','parent_run_id','usage','usage_complete')}}
        except (ValueError,OSError,RuntimeError) as error:
            return {'ok':False,'error':str(error),'status':'persistence_or_identity_failure','started':started}
