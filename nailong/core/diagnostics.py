"""Local diagnostics and transparent immutable usage accounting; no network calls."""
from __future__ import annotations
import importlib.metadata
import importlib.util
import math
import os
import shutil
import sys
import shlex
from pathlib import Path
from urllib.parse import urlsplit
from nailong.core.preferences import PreferenceStore
from nailong.core.costs import CostEstimator


def terminal_capabilities():
    terminal=sys.stdin.isatty() and sys.stdout.isatty()
    ide=bool(os.environ.get('PYCHARM_HOSTED') or 'pydevd' in sys.modules)
    inline=bool(terminal and not ide)
    fullscreen=inline and os.environ.get('TERM','').lower() not in {'','dumb'}
    if sys.platform=='darwin':
        program=os.environ.get('TERM_PROGRAM','')
        fullscreen=fullscreen and bool(program) and not any(name in program.casefold() for name in ('pycharm','jetbrains'))
    return {'tty':terminal,'inline':inline,'fullscreen':bool(fullscreen),'term':os.environ.get('TERM',''),
            'no_color':bool(os.environ.get('NO_COLOR'))}


def usage_snapshot(store,thread_id):
    totals={key:0 for key in ('input_tokens','cache_hit_tokens','output_tokens')}
    cost=0.; complete=True; usage_complete=True; calls=0; models=set()
    for event in store.read_events(thread_id) if store else []:
        if event.get('kind')=='usage_missing': usage_complete=False; complete=False
        if event.get('kind')!='usage': continue
        data=event.get('data',{})
        # Aggregate child rows retain their per-call provenance; price each call separately.
        if data.get('calls'):
            calls+=len(data['calls'])
            usage_complete=usage_complete and not bool(data.get('estimated'))
            for call in data['calls']:
                result=usage_snapshot(type('Rows',(),{'read_events':lambda self,_:[{'kind':'usage','data':call}]})(),thread_id)
                for key in totals: totals[key]+=result[key]
                cost+=result['known_cost_usd']; complete=complete and result['cost_complete']
                usage_complete=usage_complete and result['usage_complete'];models.update(result['models'])
            continue
        calls+=1
        usage_complete=usage_complete and not bool(data.get('estimated'))
        values={}
        try:
            for key in totals:
                value=data.get(key,0 if key=='cache_hit_tokens' else None)
                if value is None or isinstance(value,bool) or int(value)<0 or (isinstance(value,float) and not value.is_integer()):
                    raise ValueError('无效用量')
                values[key]=int(value)
            for key in totals: totals[key]+=values[key]
        except (ValueError,TypeError,OverflowError): usage_complete=False; complete=False; continue
        if data.get('model'): models.add(data['model'])
        price=data.get('price_snapshot') or {}
        try:
            if any(isinstance(price.get(key),bool) for key in ('input_per_million','cache_hit_per_million','output_per_million')): raise ValueError()
            rates=[float(price[key]) for key in ('input_per_million','cache_hit_per_million','output_per_million')]
            if not all(math.isfinite(rate) and rate>=0 for rate in rates): raise ValueError()
            hit=min(values['input_tokens'],values['cache_hit_tokens'])
            cost+=((values['input_tokens']-hit)*rates[0]+hit*rates[1]+values['output_tokens']*rates[2])/1_000_000
        except (KeyError,TypeError,ValueError): complete=False
    return {**totals,'total_tokens':totals['input_tokens']+totals['output_tokens'],'calls':calls,
            'usage_complete':usage_complete,'cost_complete':complete and usage_complete,
            'cost_usd':cost if complete and usage_complete else None,'known_cost_usd':cost,'models':sorted(models)}


def status_snapshot(service,thread_id,runner=None):
    factory=getattr(service,'runtime_factory',None); settings=getattr(factory,'settings',None)
    model=getattr(settings,'model','未知')
    try: host=urlsplit(getattr(settings,'api_base','')).hostname or '未知'
    except ValueError: host='无效主机'
    registry=getattr(factory,'skill_registry',None)
    plans=getattr(factory,'plan_store',None); goals=getattr(factory,'goal_store',None)
    goal=goals.active() or goals.latest() if goals else None
    store=getattr(service,'session_store',None)
    name=next((row.get('name','') for row in store.list_sessions() if row['thread_id']==thread_id),'') if store else ''
    return {'model':model,'reasoning_effort':getattr(settings,'reasoning_effort','default'),
            'provider_host':host,'project':str(getattr(settings,'project_root','未知')),
            'session_name':name,'ui':getattr(service,'ui_name','headless'),
            'session':thread_id,'permission_mode':getattr(service,'permission_mode','default'),
            'run_state':getattr(runner,'state','idle'),'queue_length':len(getattr(runner,'queue',[])),
            'goal':{'id':goal.id,'state':goal.state,'round':goal.round} if goal else None,
            'skills':len(registry.list_skills()) if registry else 0,
            'terminal':terminal_capabilities(),'usage':usage_snapshot(getattr(service,'session_store',None),thread_id)}


def diagnose(project_root,*,env=None):
    root=Path(project_root).resolve()
    bootstrap_error = False
    default_reasoning = 'default'
    if env is None:
        program_root=Path(__file__).resolve().parents[2]
        try:
            from dotenv import dotenv_values
            env={**dotenv_values(program_root/'.env'),**os.environ}
        except ImportError: env=dict(os.environ)
        from nailong.core.bootstrap import BootstrapStore
        try:
            configured = BootstrapStore().read()
            if configured:
                provider = configured['provider']
                env.update(DEEPSEEK_API_KEY=provider['api_key'], DEEPSEEK_BASE_URL=provider['api_base'],
                           DEEPSEEK_MODEL=provider['model'])
                default_reasoning = configured.get('reasoning_effort', 'default')
        except (ValueError, OSError):
            bootstrap_error = True
    checks=[]
    program_root=Path(__file__).resolve().parents[2]
    source_checkout=(program_root/'pyproject.toml').is_file()
    install=('python -m pip install -e '+shlex.quote(str(program_root)) if source_checkout
             else 'python -m pip install --upgrade ignovate-harness')
    def add(name,status,detail,repair=''):
        checks.append({'name':name,'status':status,'detail':detail,'repair':repair if status!='ok' else ''})
    add('python','ok' if sys.version_info>=(3,11) else 'error',sys.version.split()[0],'使用 Python 3.11 或更新版本创建虚拟环境')
    add('interpreter','ok',sys.executable)
    add('virtualenv','ok' if sys.prefix!=sys.base_prefix else 'warning',sys.prefix,'python3.11 -m venv .venv && source .venv/bin/activate')
    add('entrypoint','ok' if shutil.which('ignovate') else 'warning',shutil.which('ignovate') or 'ignovate 不在 PATH',install)
    add('project','ok' if root.is_dir() else 'error',str(root))
    missing=[name for name in ('DEEPSEEK_API_KEY','DEEPSEEK_BASE_URL','DEEPSEEK_MODEL') if not str(env.get(name) or '').strip()]
    add('credentials','error' if missing or bootstrap_error else 'ok',
        '用户连接配置无效' if bootstrap_error else '缺少 '+', '.join(missing) if missing else '已设置；未验证网络连接',
        '运行 ignovate --setup，或在应用 .env 设置连接；再运行 ignovate doctor')
    from nailong.core.connection import validate_connection, ConfigurationError
    try:
        base, _, _ = validate_connection(env.get('DEEPSEEK_BASE_URL') or '',
                                         env.get('DEEPSEEK_MODEL') or '',
                                         env.get('DEEPSEEK_API_KEY') or '')
        add('provider','ok',urlsplit(base).hostname)
    except ConfigurationError as error:
        add('provider','error',str(error))
    try:
        requirements=((program_root/'requirements.txt').read_text(encoding='utf-8').splitlines()
                      if source_checkout else importlib.metadata.requires('ignovate-harness') or [])
    except (OSError, UnicodeError, importlib.metadata.PackageNotFoundError):
        requirements=[]
        add('dependencies','error','无法读取依赖声明；请重新安装应用',install)
    for requirement in requirements:
        if not requirement.strip() or requirement.startswith('#'): continue
        try:
            from packaging.requirements import Requirement
            declaration=Requirement(requirement)
            if declaration.marker and not declaration.marker.evaluate(): continue
            package=declaration.name
        except ImportError:
            import re
            package=re.split('[<>=!~;[]',requirement,maxsplit=1)[0].strip()
            declaration=None
        try:
            version=importlib.metadata.version(package)
            status='warning' if declaration is None else 'ok' if declaration.specifier.contains(version) else 'error'
            add(package,status,version+'；要求 '+requirement,install)
        except importlib.metadata.PackageNotFoundError: add(package,'error','未安装',install)
    try:
        preferences=PreferenceStore(root)
        preferences.default_reasoning=default_reasoning
        prefs=preferences.effective(default_model=env.get('DEEPSEEK_MODEL') or 'deepseek-flash')
        from nailong.core.verification import VerificationService
        VerificationService(root,None).list_steps()
        add('configuration','ok','用户/项目/本地配置有效；模型 '+prefs['model'])
    except (ValueError,OSError,UnicodeError,ImportError): add('configuration','error','配置格式、路径、模型或验证步骤无效；请检查配置文件','检查 ~/.nailong/preferences.json 与项目 .nailong/settings*.json；安装缺失依赖')
    add('git','ok' if shutil.which('git') else 'warning','可用' if shutil.which('git') else '未安装；diff/review 不可用')
    add('rg','ok' if shutil.which('rg') else 'warning','可用' if shutil.which('rg') else '未安装；使用受限 Python 搜索后端')
    try:
        from nailong.core.skills import SkillRegistry
        skills=SkillRegistry(root)
        add('skills','warning' if skills.diagnostics else 'ok',f'{len(skills.list_skills())} 可用，{len(skills.diagnostics)} 无效')
    except (ValueError,OSError,ImportError): add('skills','error','Skills 无法安全加载','检查 /skills 诊断路径及 SKILL.md 的 YAML frontmatter；安装 PyYAML')
    try:
        from nailong.core.memory import MemoryStore
        from nailong.core.memory_context import memory_budget
        memory=MemoryStore(root)
        for scope in ('user','project','local'): memory.read(scope)
        maximum=memory_budget(root); catalog=memory.catalog()
        issues=catalog['diagnostics']+[row for row in catalog['documents'] if row['status']=='error']
        add('memory','warning' if issues or catalog['truncated'] else 'ok',
            f"三层记忆与 {len(catalog['documents'])} 个目录项；预算 {maximum} estimated tokens；读取异常 {len(issues)}；扫描截断 {catalog['truncated']}")
    except (ValueError,OSError,UnicodeError,ImportError): add('memory','error','记忆路径、大小、编码或预算配置不可读','检查三层 context、memory/*.md 和 memory.max_tokens；使用 UTF-8，移除链接重定向')
    encoding=getattr(sys.stdout,'encoding',None) or '未知'
    add('encoding','ok' if 'utf' in encoding.casefold() else 'warning',encoding,'设置 UTF-8 终端编码；受限终端使用 --ui plain')
    capabilities=terminal_capabilities()
    add('terminal','ok' if capabilities['inline'] else 'warning','全屏可用' if capabilities['fullscreen'] else '终端滚动可用' if capabilities['inline'] else '使用 plain；当前输入不是交互终端')
    estimator=CostEstimator(prefs['model'] if 'prefs' in locals() else env.get('DEEPSEEK_MODEL') or '',root)
    add('pricing','ok' if estimator.available else 'warning', '已配置/内置价格' if estimator.available else '价格未知；目标模式不可运行')
    for row in checks:
        if row['status']!='ok' and not row['repair']:
            row['repair']={'rg':'安装 ripgrep，或继续使用受限搜索后端',
                          'git':'安装 Git，再运行 ignovate doctor',
                          'terminal':'使用 --ui plain 或交互终端 --ui inline',
                          'provider':'DEEPSEEK_BASE_URL 设置为不含认证、查询或片段的 HTTP(S) 地址',
                          'pricing':'在项目 .nailong/settings.json 的 pricing 中配置有限非负单价',
                          'skills':'检查下方 Skill 诊断路径和 YAML frontmatter',
                          'project':'--project 指定存在且可读的项目目录'}.get(row['name'],'检查此项配置后重新运行 ignovate doctor')
    return {'project':str(root),'network_checked':False,'terminal':capabilities,'checks':checks,
            'skill_diagnostics':skills.diagnostics if 'skills' in locals() else [],
            'memory_diagnostics':issues if 'issues' in locals() else [],
            'ok':not any(row['status']=='error' for row in checks)}


def format_diagnostics(report):
    return '本地诊断（未调用 API）\n'+'\n'.join(f"[{row['status']}] {row['name']} · {row['detail']}"+(f"\n  修复：{row['repair']}" if row.get('repair') else '') for row in report['checks'])
