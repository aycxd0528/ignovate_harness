"""User-configured verification with core-owned execution and durable evidence."""
from __future__ import annotations
import asyncio
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from nailong.core.preferences import read_config, safe_config_path
from nailong.core.permissions import Decision, ApprovalDecision, PermissionEngine
from nailong.core.processes import execute_process, probe_address, ProcessCancelled
from nailong.core.process_io import join_cleanup
from nailong.tools.files import FileSession


async def _owned_worker(function, *args):
    """Propagate cancellation only after background work releases its resources."""
    import threading
    from nailong.core.input_snapshot import active_snapshot_cancellation
    cancellation = threading.Event()
    token = active_snapshot_cancellation.set(cancellation)
    try:
        worker = asyncio.create_task(asyncio.to_thread(function, *args))
    finally:
        active_snapshot_cancellation.reset(token)
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError as cancelled:
        cancellation.set()
        try:
            await join_cleanup(worker)
        except Exception as error:
            # Cancellation remains the outcome; retain a late worker failure
            # as its cause rather than changing durable evidence to invalidated.
            raise cancelled from error
        raise


def _non_repository(root):
    """Missing Git is optional only when no repository boundary is possible."""
    if any(key in os.environ for key in ('GIT_DIR','GIT_COMMON_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_OBJECT_DIRECTORY')):
        return False
    root=Path(root)
    if not root.is_dir():
        return False
    for parent in (root,*root.parents):
        try:
            (parent/'.git').lstat()
        except FileNotFoundError:
            pass
        else:
            return False
        if all((parent/name).exists() for name in ('HEAD','objects','refs')):
            return False
    return True


def _git(root, *args):
    try:
        if shutil.which('git') is None and _non_repository(root):
            return b''
        # Win32 process startup cannot use an ordinary over-MAX_PATH cwd.
        # Let Git resolve its explicit project path from a short native cwd.
        base=['git','--no-pager','-c','core.fsmonitor=false']
        cwd=root
        if os.name=='nt':
            base.extend(['-c','core.longpaths=true'])
            if len(str(root).encode('utf-16-le','surrogatepass'))//2>=260:
                if _non_repository(root):return b''
                from nailong.core.safe_files import _path
                from nailong.core._win32_files import short_directory
                cwd=short_directory(_path(root))
            else:
                base.extend(['-C',str(root)])
                cwd=root.anchor
        result=subprocess.run([*base,*args],cwd=cwd,
            capture_output=True,timeout=3)
        if result.returncode==0: return result.stdout
        if b'not a git repository' in result.stderr: return b''
        if args==('rev-parse','HEAD'):
            unborn=subprocess.run([*base,'rev-parse','--verify','--quiet','HEAD'],cwd=cwd,capture_output=True,timeout=3)
            if unborn.returncode==1: return b''
        raise ValueError('无法完整读取 Git 验证状态。')
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('无法完整读取 Git 验证状态。') from error


def input_fingerprint(root, generated=()):
    """Hash all safe input files and Git HEAD/index; unknown coverage fails closed."""
    from nailong.core.input_snapshot import fingerprint
    root=Path(root).resolve()
    return fingerprint(root, generated,
        lambda: (_git(root,'rev-parse','HEAD'), _git(root,'ls-files','--stage','-z')))


def snapshot_identity(root, generated=(), artifact_sink=None, *, extra_exclusions=(), mutable_directories=()):
    from nailong.core.input_snapshot import snapshot
    root = Path(root).resolve()
    return snapshot(root, generated,
        lambda: (_git(root,'rev-parse','HEAD'), _git(root,'ls-files','--stage','-z')), artifact_sink,
        extra_exclusions=extra_exclusions, mutable_directories=mutable_directories)


def current_evidence(root, record, boundary, extra_generated=()):
    """Observe source and complete artifacts together, under a fixed private store."""
    from nailong.core.verification_artifacts import artifact_index, load_manifest, evidence_identity
    reference = record.get('verification_manifest', record.get('artifact_manifest'))
    paths = record.get('verification_generated_paths', record.get('generated_paths', []))
    if reference is None:
        if paths:
            raise ValueError('旧生成物记录缺少完整产物证明，请重新验证。')
        return input_fingerprint(root, extra_generated)
    with artifact_index() as index:
        header = load_manifest(boundary, reference, root, index)
        observed = snapshot_identity(root, index, extra_exclusions=extra_generated)
        if observed.artifacts != header['artifacts'] or observed.artifact_count != header['count']:
            raise ValueError('验证产物已变化或缺失，请重新验证。')
        return evidence_identity(observed.inputs, header['artifacts'])


class VerificationService:
    def __init__(self, project_root, session_store, goal_store=None, *, api_key='', task_store=None, steps_override=None):
        self.root=Path(project_root).resolve()
        self.store=session_store; self.goals=goal_store; self.api_key=api_key
        self.tasks=task_store
        self.steps_override=steps_override

    def list_steps(self):
        if self.steps_override is not None:
            return self.validate_steps(self.steps_override,root=self.root)
        config=read_config(self.root/'.nailong/settings.json',self.root)
        verification=config.get('verification',{})
        if not isinstance(verification,dict): raise ValueError('verification 必须是对象。')
        if set(verification)-{'steps'}: raise ValueError('verification 包含未知字段。')
        steps=verification.get('steps',[])
        return self.validate_steps(steps,root=self.root)

    @staticmethod
    def validate_steps(steps, *, root=None):
        if not isinstance(steps,list) or len(steps)>30: raise ValueError('verification.steps 必须是至多 30 项的数组。')
        names=set(); validated=[]
        for step in steps:
            if not isinstance(step,dict): raise ValueError('验证步骤必须是对象。')
            if set(step)-{'name','kind','command','timeout_seconds','stdout_contains','http_url','expected_status','generated_paths'}:
                raise ValueError('验证步骤包含未知字段。')
            name=step.get('name'); command=step.get('command'); kind=step.get('kind')
            if (not isinstance(name,str) or not name.strip() or len(name)>80 or name in names
                    or not isinstance(command,str) or not command.strip() or len(command)>8000
                    or '\0' in command or kind not in {'test','build','run'}):
                raise ValueError('验证步骤的 name、kind 或 command 无效。')
            names.add(name)
            timeout=step.get('timeout_seconds',30)
            if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not 1<=timeout<=300:
                raise ValueError('验证超时须为 1–300 秒。')
            stdout=step.get('stdout_contains'); url=step.get('http_url')
            if stdout is not None and (not isinstance(stdout,str) or not stdout or len(stdout)>4000):
                raise ValueError('stdout_contains 必须是非空且有界的文字。')
            if kind=='run' and (bool(stdout)==bool(url)): raise ValueError('run 必须选择一个 stdout_contains 或 http_url 观察条件。')
            if kind!='run' and (stdout is not None or url is not None): raise ValueError('仅 run 步骤支持启动观察。')
            if 'expected_status' in step and url is None: raise ValueError('expected_status 仅用于 HTTP 启动探测。')
            if url is not None:
                if not isinstance(url,str): raise ValueError('http_url 必须是字符串。')
                probe_address(url)
                status=step.get('expected_status')
                if isinstance(status,bool) or not isinstance(status,int) or not 100<=status<=599:
                    raise ValueError('HTTP 探测须指定 expected_status。')
            generated=step.get('generated_paths',[])
            if not isinstance(generated,list): raise ValueError('generated_paths 必须是数组。')
            for raw in generated:
                if not isinstance(raw,str) or not raw or Path(raw).is_absolute() or '..' in Path(raw).parts:
                    raise ValueError('生成物必须是项目内相对路径。')
                if not Path(raw).parts or Path(raw).parts[0] in {'.','.nailong','AGENTS.md','CLAUDE.md'} or FileSession._is_protected(Path(raw).parts[0]):
                    raise ValueError('生成物不能排除配置或记忆。')
                if root is not None:
                    # The output entry may itself be a registered link. Its
                    # parent must stay inside the safe, non-link boundary.
                    safe_config_path((Path(root)/raw).parent, Path(root))
            validated.append({**step,'timeout_seconds':timeout,'generated_paths':generated})
        return validated

    def _validate_generated(self, generated, guard):
        if not generated:
            return (*self._scan(generated, None, guard, ()), (), [], None)
        from nailong.core.verification_artifacts import artifact_index, enumerate_generated, add_generated_parents
        from nailong.core.output_migration import migrate_outputs
        tracked={os.fsdecode(item) for item in _git(self.root,'ls-files','-z').split(b'\0') if item}
        with artifact_index() as prior, artifact_index() as frozen:
            token,warnings=self.output_authority.load(prior,lambda index:migrate_outputs(
                self.store,self.root,generated,self._configuration_fingerprint,index))
            owned_parents=set()
            for raw in generated:
                parts=Path(raw).parts
                if any(Path(path).parts[:len(parts)]==parts for path in tracked):
                    raise ValueError('生成物不能匹配已跟踪的输入文件。')
                with artifact_index() as existing:
                    enumerate_generated(self.root,[raw],existing)
                    for path in existing:
                        if path not in prior:
                            detail='旧输出证明不足或路径已退役。' if warnings else ''
                            raise ValueError(detail+'生成物不能排除本次已有输入；请使用尚未生成的专用路径。')
                        frozen.add(path)
                for parent in Path(raw).parents:
                    if parent.parts and (parent in prior or not (self.root/parent).exists()):
                        owned_parents.add(parent)
            add_generated_parents(self.root,owned_parents,frozen)
            return (*self._snapshot_outputs(frozen,bool(generated),None,guard),tuple(owned_parents),warnings,token)

    def _snapshot_outputs(self, index, publish, binding, guard):
        from contextlib import nullcontext
        from nailong.core.verification_artifacts import ManifestWriter
        guard.check()
        owner = ManifestWriter(self.evidence_boundary, binding or {'project': str(self.root), 'run_id': 'preflight'}) if publish else nullcontext(None)
        with owner as writer:
            observed = snapshot_identity(self.root, index, writer.add if writer else None,
                mutable_directories=guard.protected)
            guard.check()
            reference = writer.finish(observed) if writer else None
            if writer:self.storage_warnings=(self.storage_warnings+writer.storage_warnings)[:8]
            return observed, reference, writer.preview if writer else []

    def _prepare(self, declared, guard):
        guard.start(self.root, {parent for raw in declared for parent in Path(raw).parents})
        return self._validate_generated(declared, guard)

    def _scan(self, declared, binding, guard, parents, baseline=None):
        from nailong.core.verification_artifacts import artifact_index, enumerate_generated, add_generated_parents
        from nailong.core.output_authority import remove_scope
        with artifact_index() as index:
            if baseline is not None:
                from nailong.core.verification_artifacts import load_manifest
                load_manifest(self.evidence_boundary,baseline,self.root,index)
                for raw in declared:remove_scope(index,Path(raw))
            enumerate_generated(self.root, declared, index)
            add_generated_parents(self.root, parents, index)
            return self._snapshot_outputs(index,bool(declared) or baseline is not None,binding,guard)

    def _scan_known(self, reference, guard):
        from nailong.core.verification_artifacts import artifact_index, load_manifest
        guard.check()
        with artifact_index() as index:
            header = load_manifest(self.evidence_boundary, reference, self.root, index) if reference is not None else None
            observed = snapshot_identity(self.root, index,
                mutable_directories=guard.protected)
            guard.check()
            return observed, header

    def _admit_step(self, token, binding, declared, parents, baseline, initial, guard):
        admission=None
        if token is not None:
            admission=self.output_authority.admit(token,binding,declared,parents,baseline,
                [initial.inputs,initial.stability])
        observed,_=self._scan_known(baseline,guard)
        return observed,admission

    def _observe_step(self, admission, declared, binding, guard, parents, baseline):
        observed,reference,preview=self._scan(declared,binding,guard,parents,baseline)
        token=self.output_authority.complete(admission,reference) if admission is not None else None
        return observed,reference,preview,token

    def _record_step(self, record, step, result, started_at):
        row={'name':step['name'],'kind':step['kind'],'command':step['command'],'started_at':started_at,
            'cwd':str(self.root),'finished_at':datetime.now(timezone.utc).isoformat(),**result}
        if self.api_key:
            row={key:(value.replace(self.api_key,'[密钥已隐藏]') if isinstance(value,str) else value) for key,value in row.items()}
        record['steps'].append(row)
        return row

    async def run(self, thread_id, name=None, **kwargs):
        from nailong.core.project_execution import project_execution
        from nailong.core.sessions import ProjectSessionStore
        from nailong.core.verification_directories import DirectoryGuard
        # Event sinks may be non-durable; project execution still needs a lease.
        execution_store = self.store if hasattr(self.store, 'root') else ProjectSessionStore(self.root)
        self.evidence_boundary = execution_store.root
        self.storage_warnings=[]
        from nailong.core.output_authority import OutputAuthority
        self.output_authority = OutputAuthority(self.evidence_boundary,self.root)
        with project_execution(execution_store):
            guard = DirectoryGuard()
            try:
                return await self._run_unlocked(thread_id, name, _directory_guard=guard, **kwargs)
            finally:
                if await join_cleanup(asyncio.create_task(asyncio.to_thread(guard.close))):
                    raise asyncio.CancelledError()

    async def _run_unlocked(self, thread_id, name=None, *, approval=None, emit=None,
                  permission_engine=None, permission_mode='default', _directory_guard=None):
        steps=self.list_steps()
        if not steps: raise ValueError('尚未配置验证。请在 .nailong/settings.json 设置 verification.steps，例如：{"verification":{"steps":[{"name":"test","kind":"test","command":"python -m unittest discover -s tests","timeout_seconds":30}]}}。检查命令后重新 /verify。')
        selected=[step for step in steps if name is None or step['name']==name]
        if not selected: raise ValueError('找不到指定的验证步骤。')
        if self.tasks is not None and self.tasks.snapshot(thread_id) is not None:
            self.tasks.configure_verification(thread_id, steps)
            self.tasks.set_state(thread_id, phase='verify')
        task = self.tasks.snapshot(thread_id) if self.tasks is not None else None
        generated=sorted({raw for step in steps for raw in step['generated_paths']})
        self._configuration_fingerprint=hashlib.sha256(json.dumps(steps,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        initial, known_reference, known_preview, owned_parents, warnings, authority_token = await _owned_worker(self._prepare, generated, _directory_guard)
        before=initial.inputs
        record={'run_id':uuid.uuid4().hex,'thread_id':thread_id,'cwd':str(self.root),
            'started_at':datetime.now(timezone.utc).isoformat(),'status':'running','complete':name is None,
            'input_before':before,'declared_generated_paths':generated,'generated_paths':[],'steps':[]}
        record['artifact_authority_warnings'] = warnings
        from nailong.core.input_snapshot import INPUT_FINGERPRINT_VERSION
        record['input_fingerprint_version'] = INPUT_FINGERPRINT_VERSION
        record.update(task_id=task['task_id'] if task else None, task_revision=task['revision'] if task else None,
            task_scope=list(task['scope']) if task else [])
        record['configuration_fingerprint']=self._configuration_fingerprint
        if generated:record['output_authority_version']=1
        record['git_before']={'head':_git(self.root,'rev-parse','HEAD').decode(errors='replace').strip(),
            'index_fingerprint':hashlib.sha256(_git(self.root,'ls-files','--stage','-z')).hexdigest()}
        goal=self.goals.active() if self.goals else None
        record['goal_id']=goal.id if goal and goal.thread_id==thread_id else None
        if self.goals: self.goals.invalidate_verification(thread_id=thread_id)
        engine=permission_engine or PermissionEngine(self.root)
        try:
            for index,step in enumerate(selected):
                started_at=datetime.now(timezone.utc).isoformat()
                row=None
                binding={'run_id':record['run_id'],'step_index':index+1,
                    'configuration':record['configuration_fingerprint'],'task_id':record['task_id'],
                    'task_revision':record['task_revision'],'thread_id':thread_id}
                step_parents=tuple(parent for parent in owned_parents
                    if any(parent in Path(raw).parents for raw in step['generated_paths']))
                action={'name':'run_command','args':{'command':step['command']},'description':'执行项目验证',
                        'preview':{'command':step['command'],'cwd':str(self.root),'timeout_seconds':step['timeout_seconds']}}
                from nailong.core.stage_constraints import compile_stage_policy
                policy_task=self.tasks.snapshot(thread_id) if self.tasks is not None else None
                stage_denial=compile_stage_policy(policy_task).check('run_command',action['args'])
                decision=engine.decide_action('run_command',action['args'],mode=permission_mode)
                allowed=decision.decision==Decision.ALLOW and stage_denial is None
                if decision.decision==Decision.ASK and approval is not None and stage_denial is None:
                    choice=approval(action,index+1,len(selected))
                    if inspect.isawaitable(choice): choice=await choice
                    if isinstance(choice,ApprovalDecision):
                        allowed=choice.kind in {'approve_once','approve_session'}
                        if choice.kind=='approve_session' and choice.rule: engine.grant_session(choice.rule)
                    else: allowed=choice in {'approve','approve_once','approve_session'}
                if not allowed:
                    result={'ok':False,'exit_code':None,'output':'未批准，未执行。','timed_out':False,'observed':None,'started':False}
                    if stage_denial:result.update(error_code='stage_restricted',output=stage_denial.get('error','当前用户阶段不允许运行此验证。'))
                else:
                    try:
                        from nailong.tools.coordination import project_coordinator
                        async with project_coordinator(self.root).async_scope():
                            checked, admission = await _owned_worker(self._admit_step, authority_token,
                                binding,step['generated_paths'],step_parents,known_reference,initial,_directory_guard)
                            unchanged = checked.inputs == before and checked.stability == initial.stability
                            current_task=self.tasks.snapshot(thread_id) if self.tasks is not None else None
                            task_matches = (task is None and current_task is None or task is not None and current_task is not None
                                and (task['task_id'],task['revision'])==(current_task['task_id'],current_task['revision']))
                            same_configuration = self.list_steps()==steps
                            current_permission=engine.decide_action('run_command',action['args'],mode=permission_mode)
                            current_stage=compile_stage_policy(current_task).check('run_command',action['args'])
                            still_allowed=(current_permission.decision == Decision.ALLOW or
                                decision.decision == Decision.ASK and current_permission == decision)
                            from nailong.core.session_coordination import active_dispatch
                            dispatch=active_dispatch.get()
                            if dispatch:dispatch[0].guard(dispatch[1])
                            if not (task_matches and same_configuration and unchanged and still_allowed and current_stage is None):
                                result={'ok':False,'started':False,'exit_code':None,'timed_out':False,'observed':None,
                                    'error_code':'approval_invalidated','output':'审批绑定的输入、任务、配置或权限已变化；未启动。'}
                            else:
                                try:
                                    result=await execute_process(step['command'],self.root,timeout=step['timeout_seconds'],
                                        stdout_contains=step.get('stdout_contains'),http_url=step.get('http_url'),expected_status=step.get('expected_status',200))
                                except ProcessCancelled as error:
                                    result=error.result
                                row=self._record_step(record,step,result,started_at)
                                if result.get('cancelled'):raise asyncio.CancelledError()
                                # Timeout cleanup cannot prove completion of side effects.
                                if result.get('started') and not result.get('timed_out'):
                                    _,known_reference,known_preview,authority_token=await _owned_worker(
                                        self._observe_step,admission,step['generated_paths'],{'project':str(self.root),**binding},
                                        _directory_guard,step_parents,known_reference)
                                    if authority_token is not None:record['output_authority_token']=authority_token
                    except ProcessCancelled as error:
                        result=error.result
                if row is None:row=self._record_step(record,step,result,started_at)
                if result.get('cancelled'): raise asyncio.CancelledError()
                if emit:
                    from agent_service import TurnEvent
                    value=emit(TurnEvent('verification_step',row))
                    if inspect.isawaitable(value): await value
            final, header = await _owned_worker(self._scan_known, known_reference, _directory_guard)
            reference, preview = known_reference, known_preview
            record['generated_paths'] = preview
            record['generated_count'] = final.artifact_count
            record['generated_preview_complete'] = len(preview) == final.artifact_count
            record['source_before'] = before
            record['source_after'] = final.inputs
            record['input_after'] = final.inputs
            if reference is not None:
                from nailong.core.verification_artifacts import evidence_identity, copy_manifest
                record['artifact_manifest'] = reference
                record['input_after'] = evidence_identity(final.inputs, final.artifacts)
                if before == final.inputs:
                    record['input_before'] = record['input_after']
                if self.goals is not None:
                    await _owned_worker(copy_manifest, self.evidence_boundary, reference, self.root, self.goals.path.parent)
            record['git_after']={'head':_git(self.root,'rev-parse','HEAD').decode(errors='replace').strip(),
                'index_fingerprint':hashlib.sha256(_git(self.root,'ls-files','--stage','-z')).hexdigest()}
            if (before != final.inputs or initial.stability != final.stability
                    or header is not None and (final.artifacts != header['artifacts'] or final.artifact_count != header['count'])):
                record['status']='invalidated'
            elif any(not row['started'] for row in record['steps']): record['status']='unverified'
            elif not all(row['ok'] for row in record['steps']): record['status']='failed'
            else: record['status']='passed' if name is None else 'partial'
            if self.goals and record['status']=='passed': self.goals.record_verified_run(record)
            return record
        except asyncio.CancelledError:
            record['status']='cancelled'
            raise
        except Exception:
            record['status']='invalidated'
            raise
        finally:
            record['finished_at']=datetime.now(timezone.utc).isoformat()
            record['artifact_storage_warnings']=self.storage_warnings[:8]
            if self.tasks is not None:
                self.tasks.record_verification(thread_id, record)
            if self.store: self.store.append_event(thread_id,'verification',record)

async def verify_goal_if_configured(service,factory,goal,thread_id,*,approval=None,emit=None):
    settings=getattr(factory,'settings',None)
    if settings is None or getattr(factory,'goal_store',None) is None: return None
    verification=VerificationService(settings.project_root,getattr(service,'session_store',None),factory.goal_store,
        api_key=settings.api_key,task_store=getattr(factory,'task_store',None))
    task=verification.tasks.snapshot(thread_id) if verification.tasks else None
    intake=(task or {}).get('intake',{})
    if intake.get('stage')=='executing' and intake.get('verification'):
        verification.steps_override=intake['verification']
    if not verification.list_steps(): return None
    current=factory.goal_store.get(goal.id)
    if current is None or current.state!='active': return None
    if current.verification_tool=='verify' and current.verification_succeeded:
        try: valid=await _owned_worker(current_evidence,current.verification_project_root,
            {'verification_generated_paths':current.verification_generated_paths,
             'verification_manifest':current.verification_manifest},factory.goal_store.path.parent)==current.verification_fingerprint
        except (ValueError,OSError): valid=False
        if verification.tasks is not None:
            task=verification.tasks.snapshot(thread_id)
            valid=valid and task is not None and (task['task_id'],task['revision'])==(
                current.verification_task_id,current.verification_task_revision)
        if valid: return None
    return await verification.run(thread_id,approval=approval,emit=emit,
        permission_engine=getattr(service,'permission_engine',None),permission_mode=getattr(service,'permission_mode','default'))
