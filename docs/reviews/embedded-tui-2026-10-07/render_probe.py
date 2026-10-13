"""Offline screenshots and bounds evidence; never constructs or calls a model."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from time import perf_counter
from statistics import median

from config import Settings
from textual.widgets import Input, OptionList, Button
from ui.setup import SetupApp
from nailong.core.bootstrap import BootstrapStore
from ui.theme import load_theme
from ui.model_view import ModelScreen
from ui.settings_view import SettingScreen
from ui.help_view import HelpScreen
from ui.content_panels import PlanReviewScreen, PlanEditScreen, CopyReplyScreen, RewindConfirmationScreen
from ui.transcript import TranscriptLog, AssistantTurn
from nailong.core.reasoning import reasoning_options
from tui import TerminalAgentApp
from rich.markdown import Markdown

OUT = Path(__file__).resolve().parent
observations = []

def save(app, name, controls=()):
    app.save_screenshot(name+'.svg', path=str(OUT))
    rows = []
    for selector in controls:
        widget = app.query_one(selector)
        rows.append({'selector':selector, 'region':list(widget.region), 'visible':widget.visible,
                     'inside_screen':widget.region.bottom<=app.size.height and widget.region.right<=app.size.width})
        assert rows[-1]['inside_screen'], (name,selector,widget.region)
        ancestor=widget.parent
        while ancestor is not None and ancestor is not app.screen:
            assert widget.region.y>=ancestor.content_region.y and widget.region.bottom<=ancestor.content_region.bottom, (name,selector,widget.region,ancestor,ancestor.content_region)
            ancestor=ancestor.parent
        assert widget.content_region.height>0, (name,selector,'empty content')
    observations.append({'name':name, 'size':list(app.size), 'screen_stack':len(app.screen_stack),
                         'focus':getattr(app.focused,'id',None), 'controls':rows})

async def main():
    for name, ascii_only, no_color in [('dark',False,False),('light',False,False),('dark',True,True)]:
        mode = 'ascii-no-color' if ascii_only else name
        if no_color: os.environ['NO_COLOR']='1'
        else: os.environ.pop('NO_COLOR',None)
        for size in ((213,56),(100,32),(80,24),(60,18)):
            theme = load_theme(name=name,ascii_only=ascii_only)
            suffix = mode+'-'+str(size[0])+'x'+str(size[1])
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                app=SetupApp(store=BootstrapStore(root/'bootstrap'),project_root=root,theme=theme)
                async with app.run_test(size=size) as pilot:
                    await pilot.pause();save(app,'setup-welcome-'+suffix,('#setup-keys',))
                    await pilot.press('enter')
                    app.query_one('#setup-base',Input).value='invalid'
                    app.next_step();await pilot.pause()
                    save(app,'setup-error-'+suffix,('#setup-next','#setup-keys'))
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                settings=Settings('fixture-key','https://api.invalid','deepseek-flash',root,
                                  reasoning_effort='max', cli_preferences={'theme':name})
                app=TerminalAgentApp(SimpleNamespace(runtime_factory=None,session_store=None),settings)
                # Offline fixture stores use only this temporary project directory.
                app.actions.preferences.effective(settings.model,cli={'theme':name})
                async with app.run_test(size=size) as pilot:
                    app._ui_theme=theme;app._refresh_preferences()
                    if ascii_only:
                        app._ui_theme=theme
                    await pilot.pause();save(app,'conversation-empty-'+suffix,('#composer-info','#composer-frame'))
                    app._append_user('介绍这个项目')
                    app._append_assistant('项目入口是 `main.py`。\n\n可以读代码、检查测试和执行任务。')
                    for count in (0,2,14):
                        prefs={'model_name':'default','model':'deepseek-flash','models':{}}
                        if count:
                            prefs['models']={'default':{'model':'deepseek-flash'}, **{f'model-{i}':{'model':f'deepseek-model-{i}'} for i in range(count-1)}}
                        panel=ModelScreen(prefs)
                        task=asyncio.create_task(app._wait_panel(panel));await pilot.pause()
                        save(app,'models-'+str(count)+'-'+suffix,('#model-add','#model-use','#model-keys'))
                        if count==2:
                            await pilot.click('#model-add');await pilot.pause()
                            save(app,'model-form-'+suffix,('#model-save','#model-form-cancel'))
                        panel.complete(None);await task
                    panels=[('reasoning',SettingScreen('推理强度','high',reasoning_options('deepseek-flash')),('#setting-keys',)),
                            ('help',HelpScreen(app.controller.specs(),theme,initial_tab='commands'),('#help-footer',)),
                            ('plan-review',PlanReviewScreen('# 计划\n\n完整步骤\n'*50),('#plan-reject','#plan-approve')),
                            ('plan-edit',PlanEditScreen('# 草稿\n'*100),('#plan-edit-save','#plan-edit-cancel')),
                            ('rewind',RewindConfirmationScreen(),('#cancel','#confirm')),
                            ('body',CopyReplyScreen(lambda:['可拖选正文\n'*100],theme),('#copy-close',))]
                    for label,panel,controls in panels:
                        task=asyncio.create_task(app._wait_panel(panel));await pilot.pause()
                        save(app,label+'-'+suffix,controls)
                        panel.complete(None);await task
                    approval=app.query_one('#approval-panel')
                    approval.configure({'name':'run_command','args':{'command':'python -m unittest','timeout_seconds':15},'_approval':{'reason':'执行项目测试','preview':{'cwd':str(root)}}},1,1,root,theme)
                    approval.display=True;approval.focus_choices();await pilot.pause()
                    save(app,'approval-'+suffix,('#approval-choices','#approval-hint'))
    os.environ.pop('NO_COLOR',None)
    with tempfile.TemporaryDirectory() as directory:
        settings=Settings('fixture','https://api.invalid','deepseek-flash',Path(directory))
        app=TerminalAgentApp(SimpleNamespace(runtime_factory=None,session_store=None),settings)
        async with app.run_test(size=(100,32)) as pilot:
            log=app.query_one(TranscriptLog);log.clear()
            for i in range(300):
                log.write(Markdown(f'### 完成的回复 {i}\n\n解释 **项目结构**，以及完整测试验证。\n```python\nprint("done")\n```'))
            live=AssistantTurn(app._ui_theme);log.write(live);await pilot.pause()
            times=[]
            for i in range(10):
                live.progress=f'正在推理 {i}'
                log._dirty_entries.add(300)
                start=perf_counter();log._refresh_cached();times.append((perf_counter()-start)*1000)
            save(app,'long-conversation-dark-100x32',('#composer-info',))
            observations.append({'performance':'300 Markdown entries + active turn cached update','median_ms':median(times),'samples_ms':times})
    (OUT/'observations.json').write_text(json.dumps(observations,ensure_ascii=False,indent=2))
    print(json.dumps({'screenshots':len(list(OUT.glob('*.svg'))),'observations':len(observations),'cached_update_median_ms':observations[-1]['median_ms']}))

asyncio.run(main())
