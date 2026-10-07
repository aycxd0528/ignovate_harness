"""Shared workflow commands; UI adapters only provide rendering and interaction."""
from __future__ import annotations
import difflib
import inspect
import json
import shlex
from dataclasses import dataclass,field,replace
from pathlib import Path
from nailong.core.preferences import PreferenceStore
from nailong.core.permissions import Decision,ApprovalDecision
from nailong.core.diagnostics import diagnose,format_diagnostics,status_snapshot,usage_snapshot
from nailong.core.git_changes import GitChanges
from nailong.core.verification import VerificationService
from nailong.core.session_actions import SessionActions
from nailong.core.memory import MemoryStore
from nailong.core.costs import CostEstimator
from ui.controller import CommandRequest

WORKFLOW_COMMANDS={'/diff','/verify','/doctor','/status','/reload-skills','/skills','/stop','/queue',
                   '/rename','/recap','/export','/trace','/model','/reasoning','/permissions','/theme','/config','/memory','/tools','/cost','/context','/sessions','/task','/mcp'}
IMMEDIATE_COMMANDS={'/status','/cost','/context','/stop','/queue','/tools','/help'}
IDENTITY_COMMANDS={'/clear','/resume','/project','/rewind','/exit'}

def pause_session_goal(service, thread_id):
    """Stop only the active goal owned by the interrupted session."""
    goals=getattr(getattr(service,'runtime_factory',None),'goal_store',None)
    goal=goals.active() if goals else None
    if goal and goal.thread_id==thread_id:
        goals.update(goal.id,state='paused',summary='用户停止当前运行。',thread_id=thread_id)
    tasks=getattr(service,'task_store',None)
    if tasks is not None and thread_id and tasks.snapshot(thread_id) is not None:
        tasks.set_state(thread_id,lifecycle='paused',blockers=['用户停止当前运行。'])

@dataclass
class CommandResult:
    text: str=''
    model_requests: list=field(default_factory=list)
    refresh: bool=False
    data: dict=field(default_factory=dict)

class CommandActions:
    def __init__(self,controller):
        self.controller=controller; self.preferences=PreferenceStore(controller.settings.project_root)
        self.preferences.default_model=controller.settings.environment_model or controller.settings.model
        self.preferences.default_reasoning=controller.settings.reasoning_effort
        self.preferences.effective(cli=getattr(controller.settings,"cli_preferences",{}))
        self.session_query=''
    def handles(self,request):
        return request.command in WORKFLOW_COMMANDS or (request.command=='/review' and request.kind=='local')
    async def execute(self,request,service,thread_id,*,approval=None,emit=None,edit=None,runner=None,model_dialog=None,setting_dialog=None):
        command=request.command; args=shlex.split(request.argument)
        factory=getattr(service,'runtime_factory',None)
        if getattr(factory,'preferences',None) is not None:
            self.preferences=factory.preferences
        settings=getattr(factory,'settings',None) or self.controller.settings
        root=settings.project_root; store=getattr(service,'session_store',None)
        if command == '/permissions':
            from nailong.core.reasoning import PERMISSION_LABELS, PERMISSION_OPTIONS
            from ui.settings_flow import setting_listing
            current = getattr(service, 'permission_mode', 'default')
            if args == ['rules']:
                return CommandResult(service.get_permission_summary())
            if not args:
                if setting_dialog is None:
                    return CommandResult(setting_listing('权限', current, PERMISSION_OPTIONS)+'\n'+service.get_permission_summary())
                choice = await setting_dialog('权限', current, PERMISSION_OPTIONS)
                if choice is None:
                    return CommandResult('已取消权限设置。\n'+service.get_permission_summary())
            elif len(args) == 1:
                aliases = {'request':'default', 'assist':'acceptEdits', 'full':'bypassPermissions',
                           **{value:key for key,value in PERMISSION_LABELS.items()}}
                choice = aliases.get(args[0], args[0])
            else:
                raise ValueError('用法：/permissions [request|assist|full|rules]；权限仅对本次启动生效。')
            service.set_permission_mode(choice)
            if store is not None:
                store.append_event(thread_id, 'permission_mode_changed', {'mode': choice})
            return CommandResult('权限已设置为 '+PERMISSION_LABELS[choice]+'，仅本次启动生效。',
                                 refresh=True, data={'permission_mode':choice})
        if command == '/mcp':
            from ui.mcp_flow import execute_mcp
            manager = getattr(factory, 'mcp_manager', None)
            if manager is None:
                raise ValueError('当前运行时没有 MCP 客户端；请重新启动程序。')
            self.controller.mcp_manager = manager
            return await execute_mcp(request.argument, manager,
                permission_mode=getattr(service, 'permission_mode', 'default'))
        if command=='/task':
            tasks=getattr(factory,'task_store',None)
            if tasks is None:
                raise ValueError('当前运行时没有任务存储。')
            operation=args[0] if args else 'status'
            if operation=='requirements' and len(args)==1:
                from nailong.core.task_requirements import requirement_records
                task=tasks.snapshot(thread_id)
                if task is None:
                    raise ValueError('当前会话没有任务。')
                rows=requirement_records(task)
                return CommandResult('\n'.join(
                    f"{row['id']} [{row['status']}] {row['text']}"+
                    (f" → {row['superseded_by']}" if row['superseded_by'] else '') for row in rows),
                    data={'requirements':rows})
            if operation=='status' and len(args)<=1:
                task=tasks.snapshot(thread_id)
                if task is None:
                    return CommandResult('当前会话没有工程任务。')
                report=await service.delivery_report(thread_id)
                task=tasks.snapshot(thread_id)
                from nailong.core.delivery import render_delivery_report
                lines=[f"目标：{task['objective']}", f"阶段：{task['phase']} · 状态：{task['lifecycle']} · 要求版本：{task['revision']}",
                    '范围：'+', '.join(task['scope'])]
                lines.extend('约束：'+item for item in task['constraints'])
                lines.extend(f"步骤 {row['id']} [{row['state']}] {row['title']}" for row in task['steps'])
                lines.extend(f"验收 {row['id']} [{row['status']}] {row['description']}" for row in task['acceptance'])
                if report: lines.append(render_delivery_report(report))
                return CommandResult('\n'.join(lines),data={'task':task,'delivery':report})
            if operation in {'new','amend'} and len(args)>1:
                objective=' '.join(args[1:])
                task=tasks.begin(thread_id,objective,new=True) if operation=='new' else tasks.amend(thread_id,objective=objective)
            elif operation=='replace' and len(args)>2:
                task=tasks.replace_requirement(thread_id,args[1],' '.join(args[2:]))
            elif operation=='scope' and len(args)>1:
                task=tasks.amend(thread_id,scope=args[1:])
            elif operation=='constraint' and len(args)>1:
                current=tasks.snapshot(thread_id)
                if current is None: raise ValueError('请先 /task new <目标>。')
                task=tasks.amend(thread_id,constraints=[*current['constraints'],' '.join(args[1:])])
            elif operation=='accept' and len(args)>3:
                if args[1].startswith('verify:'): raise ValueError('verify: 前缀由项目验证配置保留。')
                task=tasks.add_acceptance(thread_id,args[1],' '.join(args[3:]),kind=args[2])
            elif operation=='bind' and len(args)>3:
                steps=VerificationService(root,store).list_steps()
                tasks.configure_verification(thread_id,steps)
                task=tasks.bind_verification(thread_id,args[1],args[2],args[3:])
            elif operation=='step' and len(args)>3:
                task=tasks.set_step(thread_id,args[1],state=args[2],title=' '.join(args[3:]))
            elif operation=='phase' and len(args)==2:
                task=tasks.set_state(thread_id,phase=args[1])
            elif operation in {'confirm','waive'} and len(args)>2:
                current=tasks.snapshot(thread_id)
                if current is None: raise ValueError('当前会话没有任务。')
                fingerprint=await service._task_input_fingerprint(current)
                task=tasks.user_decision(thread_id,args[1],' '.join(args[2:]),
                    current_input_fingerprint=fingerprint,waive=operation=='waive')
                store.append_event(thread_id,'task_user_decision',
                    {'task_id':task['task_id'],'revision':task['revision'],'acceptance_id':args[1],
                     'decision':operation,'reason':' '.join(args[2:])})
            elif operation=='complete' and len(args)==1:
                current=tasks.snapshot(thread_id)
                if current is None: raise ValueError('当前会话没有任务。')
                fingerprint=await service._task_input_fingerprint(current)
                tasks.reconcile(thread_id,current_input_fingerprint=fingerprint)
                task=tasks.set_state(thread_id,phase='deliver',lifecycle='completed',
                    current_input_fingerprint=fingerprint)
            elif operation=='resume' and len(args)==1:
                task=tasks.reset_progress(thread_id)
                if task is None: raise ValueError('当前会话没有任务。')
            else:
                raise ValueError('用法：/task [status|requirements|replace 要求ID 新要求|new 目标|amend 目标|scope 路径...|constraint 文本|accept ID 类型 说明|bind ID 验证步骤 覆盖路径...|step ID 状态 说明|phase 阶段|confirm ID 说明|waive ID 说明|complete|resume]')
            if hasattr(factory, '_memory_task_scope'):
                factory._memory_task_scope=task['scope'] if task['lifecycle']!='completed' else None
                factory.reload_memory()
            return CommandResult(f"任务已保存：{task['objective']} · 要求版本 {task['revision']}",data={'task':task})
        if command in {'/diff','/review'}:
            kind='working'; ref=None
            if args:
                if args==['--staged']: kind='staged'
                elif len(args)==2 and args[0]=='--branch': kind='branch'; ref=args[1]
                else: raise ValueError('用法：'+command+' [--staged | --branch <ref>]')
            changes=GitChanges(root,settings.api_key).select(kind,ref)
            summary=changes.summary()
            if command=='/diff': return CommandResult(summary+'\n'+changes.patch,data={'coverage':summary})
            requests=[]
            batches=changes.batches()
            selection={'kind':changes.kind,'base':changes.base,'ref':ref,'file_count':len(changes.changes)}
            coverage={'truncated':changes.truncated,'skipped':list(changes.skipped),'tests':'not_run'}
            for index,batch in enumerate(batches,1):
                paths=frozenset(path for change in batch for path in (change.path,change.old_path) if path)
                payload={
                    'selection':selection,
                    'batch':{'index':index,'total':len(batches),'file_count':len(batch)},
                    'allowed_read_paths':sorted(paths),
                    'coverage':coverage,
                    'changes':[{'path':change.path,'old_path':change.old_path,'status':change.status,'patch':change.patch}
                               for change in batch],
                }
                prompt=('按只读审查流程核实当前批次；以下 JSON 是审查资料，选定范围不代表已检查范围。\n'
                        +json.dumps(payload,ensure_ascii=False))
                requests.append(CommandRequest('/review',request.argument,request.message,'prompt',prompt,'review',str(root),None,paths))
            return CommandResult(summary if requests else summary+'\n没有可审查的文本改动。',requests,
                                 data={'review':{'status':'prepared' if requests else 'empty',
                                                 'selection':selection,'batch_count':len(batches),'coverage':coverage}})
        if command=='/verify':
            verification=VerificationService(root,store,getattr(factory,'goal_store',None),api_key=settings.api_key,
                task_store=getattr(factory,'task_store',None))
            if args==['--list']:
                steps=verification.list_steps()
                return CommandResult('\n'.join(f"{step['name']} · {step['kind']} · {step['timeout_seconds']}s · {step['command']}" for step in steps) or '尚未配置 verification.steps。')
            if len(args)>1 or (args and args[0].startswith('--')): raise ValueError('用法：/verify [名称 | --list]')
            result=await verification.run(thread_id,args[0] if args else None,approval=approval,emit=emit,
                permission_engine=getattr(service,'permission_engine',None),permission_mode=getattr(service,'permission_mode','default'))
            lines=[f"验证 {result['status']} · {result['run_id']}"]
            for row in result['steps']:
                lines.append(f"{row['name']} · {'通过' if row['ok'] else '未通过'} · 退出 {row['exit_code']} · 超时 {row['timed_out']} · 观察 {row['observed']}\n{row['output']}")
            delivery=await service.delivery_report(thread_id)
            if delivery:
                from nailong.core.delivery import render_delivery_report
                lines.append(render_delivery_report(delivery))
            return CommandResult('\n'.join(lines),data={**result,'delivery':delivery})
        if command=='/doctor':
            if args: raise ValueError('用法：/doctor')
            report=diagnose(root)
            return CommandResult(format_diagnostics(report),data=report)
        if command=='/status':
            if args: raise ValueError('用法：/status')
            report=status_snapshot(service,thread_id,runner)
            tasks=getattr(factory,'task_store',None)
            if tasks is not None:
                delivery=await service.delivery_report(thread_id)
                task=tasks.snapshot(thread_id)
                report.update(task=task,delivery=delivery)
            return CommandResult(json.dumps(report,ensure_ascii=False,indent=2),data=report)
        if command=='/cost':
            if args: raise ValueError('用法：/cost')
            usage=usage_snapshot(store,thread_id)
            text=f"会话（含子代理）输入 {usage['input_tokens']}（缓存 {usage['cache_hit_tokens']}） / 输出 {usage['output_tokens']} / {usage['total_tokens']} tokens。"
            text+=f" 当时单价估算 ${usage['cost_usd']:.6f}。" if usage['cost_complete'] else f" 费用不完整/未知；已知部分 ${usage['known_cost_usd']:.6f}。"
            if not usage['usage_complete']: text+='部分调用缺少用量。'
            return CommandResult(text,data=usage)
        if command=='/context':
            if args: raise ValueError('用法：/context')
            report=service.get_context_summary(thread_id)
            rows=report.get('categories',{})
            labels={'base_system':'系统','fixed_memory':'固定记忆/计划','task_context':'当前任务','skill_catalog':'Skill 目录','loaded_skills':'已加载 Skills','tool_definitions':'工具定义','tool_results':'工具结果','history':'其余对话','framing':'消息封装'}
            text='\n'.join(f"{labels.get(key,key)} · 约 {row['tokens']} tokens" for key,row in rows.items())
            text+=f"\n合计约 {report['estimated_tokens']} tokens；最近主调用实际输入 {report.get('actual_main_input_tokens') if report.get('actual_main_input_tokens') is not None else '未知'}。\n模型窗口 {report.get('context_window') or '未知'}；压缩阈值 {report.get('compact_threshold_tokens') or '未知'} tokens。"
            if report.get('source')=='actual_request':
                text+=f"\n最近实际请求模式 {report.get('profile')}；工具 {', '.join(report.get('tools',[])) or '无'}；输出预留 {report.get('output_reserve')}；窗口余量 {report.get('remaining_window') if report.get('remaining_window') is not None else '未知'}。"
                if report.get('compaction'):
                    compact=report['compaction']; text+=f"\n压缩原因 {compact.get('reason')}；{compact.get('before_tokens')} → {compact.get('after_tokens')} tokens，收益 {compact.get('gain',0):.1%}。"
            if report.get('memory_budget'):
                budget=report['memory_budget']
                text+=f"\n记忆统一预算 {budget['max_tokens']} estimated tokens；固定摘要与目录约 {budget['context_tokens']}；目录 {budget['documents']} 项。"
                text+='\n'+', '.join(f"{scope}: {row['status']} ({row.get('loaded_chars',0)}/{row.get('total_chars',0)} 字符)" for scope,row in budget['scopes'].items())
            text+='\n'+report.get('method','字符估算')+'（估算，实际计量以 API usage 为准）'
            return CommandResult(text,data=report)
        if command=='/reload-skills':
            if args: raise ValueError('用法：/reload-skills')
            result=factory.reload_skills()
            return CommandResult('Skills 已刷新\n'+json.dumps(result,ensure_ascii=False,indent=2),refresh=True,data=result)
        if command=='/skills':
            registry=getattr(factory,'skill_registry',None)
            if registry is None: return CommandResult('当前运行时没有 Skill 注册表。')
            if not args:
                text='\n'.join(f"${skill.name} · {skill.source} · {'禁用' if skill.name in registry.disabled else '启用'} · {registry._versions.get(skill.name,'')[:12]}\n  {skill.path}\n  {skill.description}" for skill in registry.list_skills(include_disabled=True))
                text+='\n诊断：'+json.dumps(registry.diagnostics,ensure_ascii=False) if registry.diagnostics else ''
                return CommandResult(text or '没有本地 Skills。使用 /reload-skills 刷新。')
            if len(args)!=2 or args[0] not in {'show','enable','disable'}: raise ValueError('用法：/skills [show|enable|disable <名称>]')
            if args[0]=='show':
                result=registry.load_skill(args[1]); return CommandResult(json.dumps(result,ensure_ascii=False,indent=2),data=result)
            registry.set_enabled(args[1],args[0]=='enable'); factory._skill_catalog=registry.catalog()
            return CommandResult(f'Skill {args[1]} 已'+('启用' if args[0]=='enable' else '禁用'),refresh=True)
        if command in {'/model','/reasoning','/theme','/config'}:
            global_scope='--global' in args
            if global_scope: args.remove('--global')
            if any(arg.startswith('--') for arg in args): raise ValueError('未知配置参数。')
            prefs=self.preferences.effective()
            updated=None
            if command=='/reasoning':
                from nailong.core.reasoning import reasoning_options, REASONING_LABELS
                from ui.settings_flow import setting_listing
                options = reasoning_options(prefs['model'])
                if not args:
                    if setting_dialog is None:
                        return CommandResult(setting_listing('推理强度', prefs['reasoning_effort'], options))
                    value = await setting_dialog('推理强度', prefs['reasoning_effort'], options)
                    if value is None: return CommandResult('已取消推理设置。')
                elif len(args)==1:
                    value = args[0]
                else:
                    raise ValueError('用法：/reasoning [default|none|low|high|max] [--global]')
                if not callable(getattr(factory, 'set_reasoning', None)):
                    raise ValueError('当前运行时不支持推理设置，请重新启动。')
                key = 'reasoning_effort'
            elif command=='/model':
                from ui.model_flow import ModelChoice, model_listing
                usage='用法：/model [名称] [--global]；/model add [名称 模型ID] [--global]'
                if not args or (args==['add'] and 'add' not in prefs['models']):
                    if model_dialog is None:
                        if args: raise ValueError(usage)
                        return CommandResult(model_listing(prefs))
                    if 'commit' in inspect.signature(model_dialog).parameters:
                        def commit(choice):
                            nonlocal updated
                            updated = (self.preferences.add_model(choice.name, choice.model_id, global_scope=global_scope)
                                       if choice.model_id is not None else self.preferences.set('model', choice.name, global_scope=global_scope))
                        choice = await model_dialog(prefs, add_only=bool(args), commit=commit)
                    else:
                        choice=await model_dialog(prefs,add_only=bool(args))
                    if choice is None: return CommandResult('已取消模型设置。')
                elif len(args)==3 and args[0]=='add':
                    choice=ModelChoice(args[1],args[2])
                elif len(args)==1:
                    choice=ModelChoice(args[0])
                else:
                    raise ValueError(usage)
                key,value='model',choice.name
                if choice.model_id is not None and updated is None:
                    updated=self.preferences.add_model(choice.name,choice.model_id,global_scope=global_scope)
            elif command=='/config':
                if args and args[0]=="set": args=args[1:]
                if not args: return CommandResult(json.dumps({key:prefs[key] for key in ('model','model_name','reasoning_effort','theme','output_style','sources','models')},ensure_ascii=False,indent=2))
                if len(args)!=2 or args[0] not in {'model','theme','output_style','reasoning_effort'}: raise ValueError('用法：/config set <model|reasoning_effort|theme|output_style> <值> [--global]')
                key,value=args
            else:
                key=command[1:]
                if not args:
                    return CommandResult('当前 '+key+'：'+str(prefs.get('model_name' if key=='model' else key))+'\n'+(json.dumps(prefs['models'],ensure_ascii=False) if key=='model' else 'dark / light / ansi'))
                if len(args)!=1: raise ValueError('用法：'+command+' <值> [--global]')
                value=args[0]
            if key=='reasoning_effort' and not callable(getattr(factory,'set_reasoning',None)):
                raise ValueError('当前运行时不支持推理设置，请重新启动。')
            if updated is None: updated=self.preferences.set(key,value,global_scope=global_scope)
            if key=='reasoning_effort':
                factory.set_reasoning(updated['reasoning_effort'])
                self.controller.settings=factory.settings
            if key=='model':
                factory.set_model(updated['model'])
                self.controller.settings=factory.settings; self.controller.cost_estimator=CostEstimator(updated['model'],root)
                goals=getattr(factory,'goal_store',None)
                if goals and not self.controller.cost_estimator.available:
                    goal=goals.active()
                    if goal: goals.update(goal.id,state='paused',summary='新模型价格未知，目标已暂停。',thread_id=thread_id)
            if hasattr(factory,'output_style'): factory.output_style=updated['output_style']
            text=f"模型已切换为 {updated['model_name']} · {updated['model']}。" if key=='model' else f'{key} 已设置为 {updated[key]}。'
            if key=='reasoning_effort':
                from nailong.core.reasoning import REASONING_LABELS
                text='推理强度已设置为 '+REASONING_LABELS[updated[key]]+'。'
            if command=='/model':
                text='模型配置已保存为'+('用户默认' if global_scope else '项目默认')+'；'+text
            return CommandResult(text,refresh=True,data=updated)
        if command=='/memory':
            memory=getattr(factory,'memory_store',None) or MemoryStore(root,api_key=settings.api_key)
            snapshot=getattr(factory,'_memory_snapshot',None)
            epistemic=lambda row: (f"知识 {row.get('knowledge_state','legacy')} · 有效性 {row.get('validity','unverified')}"
                f" · 适用 {row.get('applicable')} · 来源匹配不等于事实已验证")
            if not args or args==['show'] or (len(args)==2 and args[0]=='show' and args[1] in {'user','project','local'}):
                scopes=args[1:] if len(args)==2 else ['user','project','local']
                rows=[]; reports={}
                for scope in scopes:
                    report=dict(getattr(snapshot,'reports',{}).get(scope,{}))
                    try:
                        content,version=memory.read_version(scope)
                        state='不存在' if version is None else '空文件' if not content.strip() else '尚未加载'
                        if snapshot is not None and report.get('version')!=version:
                            state='磁盘有变化，待 reload（下一轮自动刷新）'
                            report['stale']=True
                        elif report.get('status')=='truncated':
                            state=f"已截断：模型加载 {report.get('loaded_chars',0)}/{len(content)} 字符；全文可分页读取"
                        elif report.get('status')=='loaded': state='已加载'
                        report.setdefault('status','missing' if version is None else 'empty' if not content.strip() else 'unloaded')
                        report.update(disk_version=version,total_chars=len(content))
                        knowledge=memory.read_document(f'{scope}/context.md',0,256)
                        report.update({key:knowledge[key] for key in ('knowledge_state','validity','applicable','fact_verified','knowledge_issues') if key in knowledge})
                        rows.append(f"{scope} · {memory.path(scope)} · {state}\n{epistemic(report)}\n{content or '(空)'}")
                    except (OSError,UnicodeError,RuntimeError,ValueError) as error:
                        report.update(status='error',error=type(error).__name__)
                        rows.append(f'{scope} · 读取失败：{type(error).__name__}；不能视为空文件或不存在。')
                    reports[scope]=report
                budget=snapshot.report() if snapshot is not None else {}
                heading=(f"记忆预算 {budget['max_tokens']} estimated tokens；固定摘要与目录约 {budget['context_tokens']}。\n\n" if budget else '')
                return CommandResult(heading+'\n\n'.join(rows),data={'memory_budget':budget,'scopes':reports})
            if args[0]=='list':
                if (len(args)>3 or (len(args)>1 and args[1] not in {'user','project','local'} and not args[1].isdigit())
                        or (len(args)==3 and (args[1].isdigit() or not args[2].isdigit()))):
                    raise ValueError('用法：/memory list [user|project|local] [offset]')
                catalog=memory.catalog(); scope=args[1] if len(args)>1 and not args[1].isdigit() else ''
                offset=int(args[-1]) if len(args)>1 and args[-1].isdigit() else 0
                rows=[row for row in catalog['documents'] if not scope or row['scope']==scope]
                end=min(len(rows),offset+20)
                result=dict(catalog,documents=rows[offset:end],offset=offset,total_documents=len(rows),
                            truncated=end<len(rows) or catalog['truncated'],scan_truncated=catalog['truncated'],
                            next_offset=end if end<len(rows) else None)
                text='\n'.join(f"{row['document']} · {row['description']} · {row['status']} · {row['size_bytes']} bytes\n  {epistemic(row)}" for row in result['documents']) or '没有记忆文档。'
                if result['next_offset'] is not None:
                    text+=f"\n目录已截断；下一页：/memory list {scope+' ' if scope else ''}{result['next_offset']}"
                if catalog['truncated']: text+='\n目录扫描已达上限；未列出的文件不能视为不存在。'
                if catalog['diagnostics']: text+='\n目录读取诊断：'+json.dumps(catalog['diagnostics'],ensure_ascii=False)
                return CommandResult(text,data=result)
            if args[0]=='read':
                if len(args) not in {2,3} or (len(args)==3 and not args[2].isdigit()):
                    raise ValueError('用法：/memory read <user|project|local/文件名.md> [offset]')
                result=memory.read_document(args[1],int(args[2]) if len(args)==3 else 0)
                if not result['ok']: return CommandResult('读取失败：'+result['error'],data=result)
                text=f"{result['document']} · version={result['version'][:12]} · {result['offset']}/{result['total_chars']} 字符\n{epistemic(result)}\n"+result['content']
                if result['truncated']: text+=f"\n已截断；下一页：/memory read {shlex.quote(args[1])} {result['next_offset']}"
                return CommandResult(text,data=result)
            if args==['reload']:
                factory.reload_memory(); return CommandResult('固定记忆已刷新，后续请求生效。',refresh=True)
            if len(args)!=2 or args[0]!='edit' or args[1] not in {'user','project','local'}: raise ValueError('用法：/memory [show [scope]|list [scope] [offset]|read <ID> [offset]|reload|edit <scope>]')
            if edit is None: raise ValueError('当前界面不支持编辑器；手动编辑 '+str(memory.path(args[1]))+' 后 /memory reload。')
            staged=memory.stage(args[1],memory.read(args[1]))
            try:
                content=edit(staged.before_content)
                if inspect.isawaitable(content): content=await content
            except ValueError as error:
                raise ValueError(str(error)+'；可手动编辑 '+str(staged.path)+' 后 /memory reload。') from error
            if content is None or content==staged.before_content: return CommandResult('记忆编辑已取消或无改动。')
            if settings.api_key: content=content.replace(settings.api_key,'[密钥已隐藏]')
            diff=''.join(difflib.unified_diff(staged.before_content.splitlines(True),content.splitlines(True),fromfile=str(staged.path),tofile=str(staged.path)))
            action={'name':'write_file','args':{'path':str(staged.path)},'preview':{'diff':diff}}
            # User memory has an explicit exact scope; plan mode still cannot write it.
            if getattr(service,'permission_mode','default')=='plan': return CommandResult('计划模式禁止修改记忆。')
            engine=getattr(service,'permission_engine',None)
            if staged.scope!='user' and engine and engine.decide_action('write_file',action['args'],mode=service.permission_mode).decision==Decision.DENY:
                return CommandResult('权限规则禁止修改记忆。')
            value=approval(action,1,1) if approval else 'reject'
            if inspect.isawaitable(value): value=await value
            allowed=value.kind in {'approve_once','approve_session'} if isinstance(value,ApprovalDecision) else value in {'approve','approve_once','approve_session'}
            if not allowed: return CommandResult('记忆写入未批准。')
            memory.commit(replace(staged,content=content)); factory.reload_memory()
            goals=getattr(factory,'goal_store',None)
            if goals: goals.invalidate_verification(thread_id=thread_id)
            return CommandResult('记忆已保存并刷新。',refresh=True)
        if command=='/rename':
            name=' '.join(args)
            if store is None: raise ValueError('当前运行时没有会话存储。')
            return CommandResult('会话已命名：'+store.rename(thread_id,name),refresh=True)
        if command=='/sessions':
            if store is None: return CommandResult('当前运行时不支持持久会话。')
            if args and args[0]=='--search': self.session_query=' '.join(args[1:])
            elif args: self.session_query=' '.join(args)
            else: self.session_query=''
            rows=store.search(self.session_query)
            lines=[]
            for index,row in enumerate(rows,1):
                records=store.read_events(row['thread_id'])
                turns=sum(event.get('kind')=='turn_start' and event.get('data',{}).get('visible') is not False for event in records)
                usage=usage_snapshot(store,row['thread_id'])
                lines.append(f"{index}. {row.get('name') or row['thread_id']} · {row['thread_id']} · {row['updated_at']} · 轮数 {turns} · {usage['total_tokens']} tokens · {row['summary']}")
            return CommandResult('\n'.join(lines) or '没有匹配会话。',data={'sessions':rows})
        if command=='/trace':
            if store is None: raise ValueError('当前运行时没有会话存储。')
            from nailong.core.traces import TraceActions
            traces=TraceActions(root,store,api_key=settings.api_key)
            if not args:
                report=traces.latest(thread_id)
                return CommandResult(traces.render(report),data={'trace':report})
            if args[0]!='export' or len(args)>2:
                raise ValueError('用法：/trace [export [项目内路径]]')
            result=await traces.export(thread_id,args[1] if len(args)==2 else None,approval=approval,
                permission_engine=getattr(service,'permission_engine',None),permission_mode=getattr(service,'permission_mode','default'))
            text=('已导出轨迹：' if result['written'] else '未导出轨迹：')+result['path']
            if not result['written']: text+=' · '+result['reason']
            return CommandResult(text,data=result)
        if command in {'/recap','/export'}:
            if store is None: raise ValueError('当前运行时没有会话存储。')
            session_actions=SessionActions(root,store,service,api_key=settings.api_key)
            if command=='/recap':
                if args: raise ValueError('用法：/recap')
                return CommandResult(session_actions.recap(thread_id))
            if len(args)>1: raise ValueError('用法：/export [项目内路径]')
            result=await session_actions.export(thread_id,args[0] if args else None,approval=approval,
                permission_engine=getattr(service,'permission_engine',None),permission_mode=getattr(service,'permission_mode','default'))
            return CommandResult(('已导出：' if result['written'] else '未导出：')+result['path'],data=result)
        if command=='/stop':
            if args: raise ValueError('用法：/stop')
            if runner: await runner.stop()
            pause_session_goal(service,thread_id)
            return CommandResult('当前任务已停止；会话保留，待执行输入已暂停。')
        if command=='/queue':
            if runner is None: return CommandResult('当前没有输入队列。')
            if not args: return CommandResult(f'队列 {runner.state}\n'+'\n'.join(f'{index}. {item.message}' for index,item in enumerate(runner.queue,1)))
            if args==['clear']: runner.clear(); return CommandResult('待执行输入已清空。')
            if args==['resume']: runner.resume(); return CommandResult('输入队列已恢复。')
            if len(args)==2 and args[0]=='remove': runner.remove(int(args[1])); return CommandResult('待执行输入已删除。')
            raise ValueError('用法：/queue [clear|resume|remove <序号>]')
        if command=='/tools':
            from ui.presentation import render_tool_details, render_tool_line, tool_records
            rows = tool_records(store.read_events(thread_id)) if store else []
            if args:
                if len(args)!=1 or not args[0].isdigit() or not 1<=int(args[0])<=len(rows): raise ValueError('用法：/tools [序号]')
                row=rows[int(args[0])-1]
                return CommandResult(render_tool_details(row, api_key=self.controller.settings.api_key).plain)
            return CommandResult('\n'.join(f"{index}. {render_tool_line(row, api_key=self.controller.settings.api_key).plain}" for index,row in enumerate(rows,1)) or '没有工具记录。')
        raise ValueError('此命令不属于共享工作流。')
