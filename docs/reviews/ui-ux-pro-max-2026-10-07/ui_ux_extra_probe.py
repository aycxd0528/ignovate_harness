import asyncio, json, tempfile, time, statistics, xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from rich.cells import cell_len
from config import Settings
from tui import TerminalAgentApp
from ui.setup import SetupApp
from ui.settings_view import SettingScreen
from ui.transcript import AssistantTurn
from nailong.core.bootstrap import BootstrapStore
from nailong.core.reasoning import reasoning_options

out=Path('/Users/aycxd/Desktop/LLM_project/docs/reviews/ui-ux-pro-max-2026-10-07')
rows=[]

def shot(app,name):
    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    tree=ET.fromstring(app.export_screenshot(title='UI/UX audit'))
    for e in tree.iter():
        if e.tag.endswith('}text') and e.text and e.get('textLength'):
            e.set('textLength',str(float(e.get('textLength'))*cell_len(e.text)/len(e.text)))
            e.set('lengthAdjust','spacingAndGlyphs')
    (out/(name+'.svg')).write_text(ET.tostring(tree,encoding='unicode'),encoding='utf-8')

async def main():
    with tempfile.TemporaryDirectory() as root:
        settings=Settings('fixture-key','https://api.invalid','deepseek-flash',Path(root))
        app=TerminalAgentApp(SimpleNamespace(runtime_factory=None,session_store=None),settings)
        async with app.run_test(size=(80,24)) as pilot:
            screen=SettingScreen('推理强度','high',reasoning_options('deepseek-flash'))
            await app.push_screen(screen)
            await pilot.pause(0.3)
            details=[]
            for selector in ['#setting-dialog','#setting-title','#setting-options','#setting-keys']:
                w=screen.query_one(selector)
                details.append({'selector':selector,'region':list(w.region),
                    'content_region':list(w.content_region),'visible':w.visible,
                    'opacity':w.styles.opacity,'render':str(w.render()),
                    'lines':str(w.render_lines(w.content_region.reset_offset))})
            rows.append({'scene':'setting-footer','details':details})
            shot(app,'setting-footer-check-80x24')
    with tempfile.TemporaryDirectory() as root:
        app=SetupApp(store=BootstrapStore(Path(root)/'bootstrap'),
            settings=Settings('fixture-key','https://api.invalid','deepseek-flash',Path(root)))
        async with app.run_test(size=(80,24)) as pilot:
            app.show_step(1)
            app.query_one('#setup-base').value='invalid-address'
            app.query_one('#setup-next').focus()
            await pilot.press('enter')
            await pilot.pause(0.2)
            rows.append({'scene':'setup-submit-error','focused':app.focused.id,
                'error':app.query_one('#setup-error').render().plain})
            shot(app,'setup-submit-error-80x24')
    with tempfile.TemporaryDirectory() as root:
        app=TerminalAgentApp(SimpleNamespace(runtime_factory=None,session_store=None),
            Settings('fixture-key','https://api.invalid','deepseek-flash',Path(root)))
        async with app.run_test(size=(100,32)) as pilot:
            log=app.query_one('#transcript')
            log.clear()
            for i in range(300):
                reply=AssistantTurn(app._ui_theme)
                reply.text=f'检查结果 {i}\n\n这里是一段项目解释，包括 **工具调用**、配置和运行结果。\n\n- 已完成的项目检查\n- 需要继续处理的建议\n\n```python\nprint("example")\n```'
                reply.finished=True
                log.write(reply)
            await pilot.pause()
            durations=[]
            for _ in range(3):
                start=time.perf_counter()
                log._reflow()
                durations.append((time.perf_counter()-start)*1000)
            rows.append({'scene':'transcript-reflow','synthetic':True,'entries':len(log._entries),
                'median_reflow_ms':round(statistics.median(durations),1)})
    (out/'extra-observations.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(rows,ensure_ascii=False,indent=2))

asyncio.run(main())
