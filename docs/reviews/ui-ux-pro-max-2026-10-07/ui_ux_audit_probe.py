import asyncio
import json
import tempfile
import time
import statistics
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from rich.cells import cell_len
from config import Settings
from tui import TerminalAgentApp
from ui.setup import SetupApp
from ui.help_view import HelpScreen
from ui.settings_view import SettingScreen
from ui.model_view import ModelScreen
from ui.commands import COMMANDS
from ui.transcript import AssistantTurn
from nailong.core.bootstrap import BootstrapStore
from nailong.core.reasoning import reasoning_options
from agent_service import TurnEvent

OUTPUT=Path('/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07')
OUTPUT.mkdir(parents=True,exist_ok=True)
results=[]

def cjk(svg):
    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    tree=ET.fromstring(svg)
    for e in tree.iter():
        if e.tag.endswith('}text') and e.text and e.get('textLength'):
            e.set('textLength',str(float(e.get('textLength'))*cell_len(e.text)/len(e.text)))
            e.set('lengthAdjust','spacingAndGlyphs')
    return ET.tostring(tree,encoding='unicode')

def snapshot(app,name):
    (OUTPUT/(name+'.svg')).write_text(cjk(app.export_screenshot(title='ignovate harness · UI/UX 审查')),encoding='utf-8')
    focused=app.focused
    visible=[]
    for w in app.screen.query('Static, Input, OptionList, Button, TextArea'):
        if w.display and w.region.width and w.region.height:
            visible.append({'id':w.id,'region':list(w.region),'color':str(w.styles.color),
                            'background':str(w.styles.background),'focus':w is focused})
    results.append({'scene':name,'size':list(app.size),'focused':focused.id if focused else None,'widgets':visible})

async def scene(state, theme='dark', size=(80,24)):
    with tempfile.TemporaryDirectory() as root:
        settings=Settings('fixture-key','https://api.invalid','deepseek-flash',Path(root),cli_preferences={'theme':theme})
        service=SimpleNamespace(runtime_factory=None,session_store=None,permission_mode='default')
        app=TerminalAgentApp(service,settings)
        async with app.run_test(size=size) as pilot:
            if state not in ('empty',):
                app._append_user('检查工具调用与界面设计')
                app._ensure_live_response()
                for i,name in enumerate(['list_files','read_file','read_file','run_command']):
                    app._append_activity(TurnEvent('tool_start',{'call_id':str(i),'name':name,
                        'preview':{'path':'ui/theme.py','command':'python -m unittest tests.test_ui'}}),[])
                    app._append_activity(TurnEvent('tool_end',{'call_id':str(i),'name':name,'ok':True,
                        'summary':'完成','elapsed_ms':90,'exit_code':0 if name=='run_command' else None}),[])
                app._append_assistant('已经检查当前终端界面。\n\n模型回复与工具摘要保持清晰分层，设置与审批可直接使用键盘操作。\n\n- 第一项结果\n- 第二项结果')
                app._set_status('ready')
                app._observe_usage(TurnEvent('usage',{'input_tokens':107300,'output_tokens':1000}))
            if state=='approval':
                panel=app.query_one('#approval-panel')
                panel.display=True
                panel.configure({'name':'run_command','args':{'command':'python -m unittest tests.test_ui --verbose','timeout_seconds':60},
                    '_approval':{'reason':'运行项目测试','suggested_rule':'Bash(python -m unittest:*)','preview':{'cwd':'/project'}}},
                    1,1,Path(root),app._ui_theme)
                panel.focus_choices()
                app._set_status('waiting_approval')
            elif state=='setting':
                await app.push_screen(SettingScreen('推理强度','high',reasoning_options('deepseek-flash')))
            elif state=='model':
                await app.push_screen(ModelScreen({'model_name':'default','model':'deepseek-flash',
                    'models':{'default':{'model':'deepseek-flash'},'pro':{'model':'deepseek-v4-pro'}}}))
            elif state=='help':
                await app.push_screen(HelpScreen(COMMANDS,app._ui_theme,initial_tab='commands'))
            await pilot.pause()
            snapshot(app,f'{state}-{theme}-{size[0]}x{size[1]}')

async def setup_scene(state,size):
    with tempfile.TemporaryDirectory() as root:
        settings=Settings('fixture-key','https://api.invalid','deepseek-flash',Path(root))
        app=SetupApp(store=BootstrapStore(Path(root)/'bootstrap'),settings=settings)
        async with app.run_test(size=size) as pilot:
            if state=='error':
                app.show_step(1)
                app.query_one('#setup-base').value='invalid-address'
                app.next_step()
            await pilot.pause()
            snapshot(app,f'setup-{state}-{size[0]}x{size[1]}')
            if state=='error':
                results[-1]['error']=app.query_one('#setup-error').render().plain
                results[-1]['focused_after_error']=app.focused.id

async def performance():
    with tempfile.TemporaryDirectory() as root:
        app=TerminalAgentApp(SimpleNamespace(runtime_factory=None,session_store=None),
            Settings('fixture-key','https://api.invalid','deepseek-flash',Path(root)))
        async with app.run_test(size=(100,32)) as pilot:
            log=app.query_one('#transcript')
            log.clear()
            turns=0
            timings=[]
            for count in (10,50,100):
                for i in range(turns,count):
                    reply=AssistantTurn(app._ui_theme)
                    reply.text=f'检查结果 {i}\n\n这里是一段项目解释，包括 **工具调用**、配置和运行结果。\n\n- 已完成的项目检查\n- 需要继续处理的建议\n\n```python\nprint("example")\n```'
                    reply.finished=True
                    log.write(reply)
                turns=count
                await pilot.pause()
                durations=[]
                for _ in range(3):
                    start=time.perf_counter()
                    log._reflow()
                    durations.append((time.perf_counter()-start)*1000)
                timings.append({'turns':count,'entries':len(log._entries),'median_reflow_ms':round(statistics.median(durations),1)})
            results.append({'scene':'transcript-reflow','synthetic':True,'timings':timings})

async def main():
    for args in [('empty','dark',(100,32)),('empty','light',(100,32)),('conversation','dark',(80,24)),
                 ('approval','dark',(80,24)),('setting','dark',(80,24)),('setting','light',(80,24)),
                 ('model','dark',(80,24)),('help','dark',(80,24))]:
        await scene(*args)
    await setup_scene('welcome',(100,32))
    await setup_scene('error',(80,24))
    await performance()
    (OUTPUT/'observations.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in results:
        print(row['scene'],row.get('focused'),row.get('focused_after_error'),row.get('timings',''))

asyncio.run(main())
