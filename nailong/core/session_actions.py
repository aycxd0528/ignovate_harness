"""Evidence-aware session recap and approved, project-scoped Markdown export."""
from __future__ import annotations
import difflib
import hashlib
import inspect
import json
import os
import tempfile
from datetime import datetime,timezone
from pathlib import Path
from nailong.core.preferences import safe_config_path
from nailong.core.permissions import PermissionEngine,Decision,ApprovalDecision
from nailong.core.diagnostics import usage_snapshot
from nailong.tools.files import FileSession


class SessionActions:
    def __init__(self,project_root,store,service=None,*,api_key=''):
        self.root=Path(project_root).resolve(); self.store=store; self.service=service; self.api_key=api_key
    def history(self,thread_id):
        if self.service:
            history=self.service.get_history(thread_id)
            return [(role,text) for role,text in history if role in {'user','assistant'} and not self._tool_json(text)]
        return [('user' if record['kind']=='user' else 'assistant',record.get('data',{}).get('text',''))
                for record in self.store.read_events(thread_id) if record.get('kind') in {'user','final'}]
    @staticmethod
    def _tool_json(text):
        try: value=json.loads(text)
        except (ValueError,TypeError): return False
        return isinstance(value,list) and bool(value) and all(isinstance(item,dict) and 'name' in item and 'args' in item for item in value)
    def recap(self,thread_id):
        events=self.store.read_events(thread_id)
        finals=[text for role,text in self.history(thread_id) if role=='assistant' and text]
        verification=[event.get('data',{}) for event in events if event.get('kind')=='verification']
        failures=[event.get('data',{}).get('summary') or event.get('data',{}).get('error') for event in events
                  if event.get('kind')=='tool_end' and event.get('data',{}).get('ok') is False]
        lines=['会话回顾（依据保存的回答与执行记录）', '最近回答：'+(finals[-1][:1800] if finals else '尚无完成的回答。')]
        factory=getattr(self.service,'runtime_factory',None)
        goals=getattr(factory,'goal_store',None)
        goal=goals.active() or goals.latest() if goals else None
        if goal and goal.thread_id==thread_id:
            lines.append('目标：'+goal.objective+' · '+goal.state)
            lines.append(('已完成事项：' if goal.state=='complete' else '待办：')+(goal.last_summary or goal.objective))
            if goal.last_blocker: lines.append('阻碍：'+goal.last_blocker)
        completed=[event['data'] for event in events if event.get('kind')=='tool_end' and event['data'].get('ok') is True and event['data'].get('name') in {'write_file','edit_file','run_command'}]
        lines.extend('已执行：'+str(item.get('name'))+' · '+str(item.get('summary',''))[:300] for item in completed[-8:])
        valid=False
        if verification:
            latest=verification[-1]
            if latest.get('status')=='passed' and latest.get('input_after'):
                from nailong.core.verification import input_fingerprint
                try: valid=input_fingerprint(self.root,latest.get('generated_paths',[]))==latest['input_after']
                except (ValueError,OSError): valid=False
            lines.append('最近验证：'+str(latest.get('status','未知')))
            lines.extend(f"  {step.get('name','?')} · {'通过' if step.get('ok') else '未通过'}" for step in latest.get('steps',[]))
        if not valid:
            lines.append('未验证：尚无完整通过的项目验证证据，请 /verify。')
        else: lines.append('已验证：完整项目验证通过，当前输入摘要仍一致。')
        if failures: lines.append('待处理执行失败：'+str(failures[-1])[:500])
        text='\n'.join(lines)
        return text.replace(self.api_key,'[密钥已隐藏]') if self.api_key else text
    async def export(self,thread_id,path=None,*,approval=None,permission_engine=None,permission_mode='default'):
        self.store.session_path(thread_id)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
        destination=Path(path) if path else Path('.nailong/exports')/f'{thread_id}-{stamp}.md'
        if not destination.is_absolute(): destination=self.root/destination
        safe_config_path(destination,self.root)
        destination=FileSession(self.root).resolve(str(destination),allow_missing=True)
        safe_config_path(destination,self.root)
        names={row['thread_id']:row.get('name','') for row in self.store.list_sessions()}
        lines=[f"# {names.get(thread_id) or 'ignovate harness 会话'}",f'\n项目：{self.root}\n\n会话：{thread_id}\n']
        for role,text in self.history(thread_id):
            lines.append('## '+('用户' if role=='user' else 'ignovate harness')+'\n\n'+text+'\n')
        usage=usage_snapshot(self.store,thread_id)
        lines.extend(['## 用量\n',f"已知 {usage['total_tokens']} tokens；费用 "+(f"${usage['cost_usd']:.6f}" if usage['cost_complete'] else '不完整/未知'),
                      '\n## 验证与待办\n',self.recap(thread_id)])
        content='\n'.join(lines)+'\n'
        if self.api_key: content=content.replace(self.api_key,'[密钥已隐藏]')
        old=destination.read_text(encoding='utf-8') if destination.exists() else None
        digest=hashlib.sha256(old.encode()).hexdigest() if old is not None else None
        engine=permission_engine or PermissionEngine(self.root)
        decision=engine.decide_action('write_file',{'path':str(destination)},mode=permission_mode)
        if decision.decision==Decision.DENY: return {'written':False,'path':str(destination),'reason':decision.reason}
        if old is not None and decision.decision!=Decision.ALLOW:
            action={'name':'write_file','args':{'path':str(destination)},'preview':{'diff':''.join(difflib.unified_diff(old.splitlines(True),content.splitlines(True),fromfile=str(destination),tofile=str(destination)))[:12000]}}
            value=approval(action,1,1) if approval else 'reject'
            if inspect.isawaitable(value): value=await value
            allowed=value.kind in {'approve_once','approve_session'} if isinstance(value,ApprovalDecision) else value in {'approve','approve_once','approve_session'}
            if not allowed: return {'written':False,'path':str(destination),'reason':'覆盖未批准。'}
        destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd,temporary=tempfile.mkstemp(dir=destination.parent,prefix='.export-')
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream: stream.write(content)
            safe_config_path(destination,self.root)
            current=hashlib.sha256(destination.read_bytes()).hexdigest() if destination.exists() else None
            if current!=digest: raise ValueError('导出目标被其他进程修改。')
            os.replace(temporary,destination)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
        return {'written':True,'path':str(destination)}
