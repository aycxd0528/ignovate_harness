"""User-configured verification with core-owned execution and durable evidence."""
from __future__ import annotations
import asyncio
import hashlib
import inspect
import json
import os
import subprocess
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

from nailong.core.preferences import read_config, safe_config_path
from nailong.core.permissions import Decision, ApprovalDecision, PermissionEngine
from nailong.core.processes import execute_process, probe_address, ProcessCancelled
from nailong.tools.files import FileSession


def _git(root, *args):
    try:
        result=subprocess.run(['git','--no-pager','-c','core.fsmonitor=false',*args],cwd=root,
            capture_output=True,timeout=3)
        if result.returncode==0: return result.stdout
        if b'not a git repository' in result.stderr: return b''
        if args==('rev-parse','HEAD'):
            unborn=subprocess.run(['git','rev-parse','--verify','--quiet','HEAD'],cwd=root,capture_output=True,timeout=3)
            if unborn.returncode==1: return b''
        raise ValueError('无法完整读取 Git 验证状态。')
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('无法完整读取 Git 验证状态。') from error


def input_fingerprint(root, generated=()):
    """Hash all safe input files and Git HEAD/index; unknown coverage fails closed."""
    root=Path(root).resolve()
    exclusions={Path(item) for item in generated}
    digest=hashlib.sha256()
    count=total=0
    def walk_error(error): raise error
    for base, directories, files in os.walk(root,followlinks=False,onerror=walk_error):
        directory=Path(base)
        relative=directory.relative_to(root)
        def excluded(path):
            return path in exclusions or path.parts[:2]==('.nailong','exports')
        for name in list(directories):
            path=directory/name
            if FileSession._is_protected(name) or excluded(path.relative_to(root)):
                directories.remove(name)
            elif path.is_symlink():
                raise ValueError('验证摘要无法覆盖符号链接目录。')
        directories.sort()
        for name in sorted(files):
            path=directory/name
            rel=path.relative_to(root)
            if FileSession._is_protected(name) or excluded(rel): continue
            safe_config_path(path,root)
            before=path.stat()
            if not path.is_file(): raise ValueError('验证摘要包含非普通文件。')
            count+=1; total+=before.st_size
            if count>5000 or total>50*1024*1024: raise ValueError('项目超过完整验证摘要的上限（5000 文件/50 MiB）。')
            file_hash=hashlib.sha256()
            import local_tools
            with local_tools.open_regular_file(path) as source:
                for chunk in iter(lambda:source.read(65536),b''): file_hash.update(chunk)
            after=path.stat()
            if (before.st_mtime_ns,before.st_size,before.st_ino,before.st_mode)!=(after.st_mtime_ns,after.st_size,after.st_ino,after.st_mode):
                raise ValueError('生成验证摘要期间输入被修改。')
            digest.update(os.fsencode(rel)+b'\0'+str(stat.S_IMODE(before.st_mode)).encode()+b'\0'+file_hash.digest())
    digest.update(_git(root,'rev-parse','HEAD'))
    digest.update(_git(root,'ls-files','--stage','-z'))
    return digest.hexdigest()


class VerificationService:
    def __init__(self, project_root, session_store, goal_store=None, *, api_key='', task_store=None):
        self.root=Path(project_root).resolve()
        self.store=session_store; self.goals=goal_store; self.api_key=api_key
        self.tasks=task_store

    def list_steps(self):
        config=read_config(self.root/'.nailong/settings.json',self.root)
        verification=config.get('verification',{})
        if not isinstance(verification,dict): raise ValueError('verification 必须是对象。')
        if set(verification)-{'steps'}: raise ValueError('verification 包含未知字段。')
        steps=verification.get('steps',[])
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
                safe_config_path(self.root/raw,self.root)
            validated.append({**step,'timeout_seconds':timeout,'generated_paths':generated})
        return validated

    def _validate_generated(self, generated):
        if not generated:
            return
        tracked={os.fsdecode(item) for item in _git(self.root,'ls-files','-z').split(b'\0') if item}
        # Existing generated outputs may be ignored only when prior successful evidence declared them.
        prior=set()
        if self.store:
            for session in self.store.list_sessions():
                for event in self.store.read_events(session['thread_id']):
                    data=event.get('data',{})
                    if event.get('kind')=='verification' and data.get('status')=='passed':
                        prior.update(data.get('generated_paths',[]))
        for raw in generated:
            parts=Path(raw).parts
            if any(Path(path).parts[:len(parts)]==parts for path in tracked):
                raise ValueError('生成物不能匹配已跟踪的输入文件。')
            path=self.root/raw
            existing=self._generated_files([raw])
            if path.exists() and not set(existing).issubset(prior):
                raise ValueError('生成物不能排除本次已有输入；请使用尚未生成的专用路径。')
            if path.is_dir():
                for base, dirs, files in os.walk(path,followlinks=False):
                    for name in dirs+files: safe_config_path(Path(base)/name,self.root)

    def _generated_files(self, declared):
        files=[]
        for raw in declared:
            path=safe_config_path(self.root/raw,self.root)
            if path.is_dir():
                def failed(error): raise error
                for base,dirs,names in os.walk(path,followlinks=False,onerror=failed):
                    for name in dirs+names: safe_config_path(Path(base)/name,self.root)
                    files.extend(str((Path(base)/name).relative_to(self.root)) for name in names)
            elif path.is_file(): files.append(str(path.relative_to(self.root)))
            elif path.exists(): raise ValueError('生成物必须是普通文件或目录。')
        return sorted(set(files))

    async def run(self, thread_id, name=None, *, approval=None, emit=None,
                  permission_engine=None, permission_mode='default'):
        steps=self.list_steps()
        if not steps: raise ValueError('尚未配置验证。请在 .nailong/settings.json 设置 verification.steps，例如：{"verification":{"steps":[{"name":"test","kind":"test","command":"python -m unittest discover -s tests","timeout_seconds":30}]}}。检查命令后重新 /verify。')
        selected=[step for step in steps if name is None or step['name']==name]
        if not selected: raise ValueError('找不到指定的验证步骤。')
        if self.tasks is not None and self.tasks.snapshot(thread_id) is not None:
            self.tasks.configure_verification(thread_id, steps)
            self.tasks.set_state(thread_id, phase='verify')
        task = self.tasks.snapshot(thread_id) if self.tasks is not None else None
        generated=sorted({raw for step in steps for raw in step['generated_paths']})
        await asyncio.to_thread(self._validate_generated, generated)
        before=await asyncio.to_thread(input_fingerprint,self.root,self._generated_files(generated))
        record={'run_id':uuid.uuid4().hex,'thread_id':thread_id,'cwd':str(self.root),
            'started_at':datetime.now(timezone.utc).isoformat(),'status':'running','complete':name is None,
            'input_before':before,'declared_generated_paths':generated,'generated_paths':[],'steps':[]}
        record.update(task_id=task['task_id'] if task else None, task_revision=task['revision'] if task else None,
            task_scope=list(task['scope']) if task else [])
        record['configuration_fingerprint']=hashlib.sha256(json.dumps(steps,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        record['git_before']={'head':_git(self.root,'rev-parse','HEAD').decode(errors='replace').strip(),
            'index_fingerprint':hashlib.sha256(_git(self.root,'ls-files','--stage','-z')).hexdigest()}
        goal=self.goals.active() if self.goals else None
        record['goal_id']=goal.id if goal and goal.thread_id==thread_id else None
        if self.goals: self.goals.invalidate_verification(thread_id=thread_id)
        engine=permission_engine or PermissionEngine(self.root)
        try:
            for index,step in enumerate(selected):
                started_at=datetime.now(timezone.utc).isoformat()
                action={'name':'run_command','args':{'command':step['command']},'description':'执行项目验证',
                        'preview':{'command':step['command'],'cwd':str(self.root),'timeout_seconds':step['timeout_seconds']}}
                decision=engine.decide_action('run_command',action['args'],mode=permission_mode)
                allowed=decision.decision==Decision.ALLOW
                if decision.decision==Decision.ASK and approval is not None:
                    choice=approval(action,index+1,len(selected))
                    if inspect.isawaitable(choice): choice=await choice
                    if isinstance(choice,ApprovalDecision):
                        allowed=choice.kind in {'approve_once','approve_session'}
                        if choice.kind=='approve_session' and choice.rule: engine.grant_session(choice.rule)
                    else: allowed=choice in {'approve','approve_once','approve_session'}
                if not allowed:
                    result={'ok':False,'exit_code':None,'output':'未批准，未执行。','timed_out':False,'observed':None,'started':False}
                else:
                    try:
                        from nailong.tools.coordination import project_coordinator
                        async with project_coordinator(self.root).async_scope():
                            unchanged = await asyncio.to_thread(input_fingerprint,self.root,self._generated_files(generated))==before
                            current_task=self.tasks.snapshot(thread_id) if self.tasks is not None else None
                            task_matches = (task is None and current_task is None or task is not None and current_task is not None
                                and (task['task_id'],task['revision'])==(current_task['task_id'],current_task['revision']))
                            same_configuration = self.list_steps()==steps
                            current_permission=engine.decide_action('run_command',action['args'],mode=permission_mode)
                            still_allowed=(current_permission.decision == Decision.ALLOW or
                                decision.decision == Decision.ASK and current_permission == decision)
                            if not (task_matches and same_configuration and unchanged and still_allowed):
                                result={'ok':False,'started':False,'exit_code':None,'timed_out':False,'observed':None,
                                    'error_code':'approval_invalidated','output':'审批绑定的输入、任务、配置或权限已变化；未启动。'}
                            else:
                                result=await execute_process(step['command'],self.root,timeout=step['timeout_seconds'],
                                    stdout_contains=step.get('stdout_contains'),http_url=step.get('http_url'),expected_status=step.get('expected_status',200))
                    except ProcessCancelled as error:
                        result=error.result
                row={'name':step['name'],'kind':step['kind'],'command':step['command'],
                     'started_at':started_at,
                     'cwd':str(self.root),'finished_at':datetime.now(timezone.utc).isoformat(),**result}
                if self.api_key:
                    row={key:(value.replace(self.api_key,'[密钥已隐藏]') if isinstance(value,str) else value) for key,value in row.items()}
                record['steps'].append(row)
                if result.get('cancelled'): raise asyncio.CancelledError()
                if emit:
                    from agent_service import TurnEvent
                    value=emit(TurnEvent('verification_step',row))
                    if inspect.isawaitable(value): await value
            record['generated_paths']=self._generated_files(generated)
            record['input_after']=await asyncio.to_thread(input_fingerprint,self.root,record['generated_paths'])
            record['git_after']={'head':_git(self.root,'rev-parse','HEAD').decode(errors='replace').strip(),
                'index_fingerprint':hashlib.sha256(_git(self.root,'ls-files','--stage','-z')).hexdigest()}
            if before!=record['input_after']: record['status']='invalidated'
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
            if self.tasks is not None:
                self.tasks.record_verification(thread_id, record)
            if self.store: self.store.append_event(thread_id,'verification',record)

async def verify_goal_if_configured(service,factory,goal,thread_id,*,approval=None,emit=None):
    settings=getattr(factory,'settings',None)
    if settings is None or getattr(factory,'goal_store',None) is None: return None
    verification=VerificationService(settings.project_root,getattr(service,'session_store',None),factory.goal_store,
        api_key=settings.api_key,task_store=getattr(factory,'task_store',None))
    if not verification.list_steps(): return None
    current=factory.goal_store.get(goal.id)
    if current is None or current.state!='active': return None
    if current.verification_tool=='verify' and current.verification_succeeded:
        try: valid=await asyncio.to_thread(input_fingerprint,current.verification_project_root,current.verification_generated_paths)==current.verification_fingerprint
        except (ValueError,OSError): valid=False
        if verification.tasks is not None:
            task=verification.tasks.snapshot(thread_id)
            valid=valid and task is not None and (task['task_id'],task['revision'])==(
                current.verification_task_id,current.verification_task_revision)
        if valid: return None
    return await verification.run(thread_id,approval=approval,emit=emit,
        permission_engine=getattr(service,'permission_engine',None),permission_mode=getattr(service,'permission_mode','default'))
