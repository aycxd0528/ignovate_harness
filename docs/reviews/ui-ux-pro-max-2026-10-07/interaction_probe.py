"""Offline UI audit fixtures: no model requests or production configuration writes."""
import asyncio
import json
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

from rich.cells import cell_len
from textual.widgets import Input, OptionList
from config import Settings
from tui import TerminalAgentApp
from ui.model_view import ModelScreen
from ui.setup import SetupApp
from nailong.core.bootstrap import BootstrapStore

OUTPUT = Path(__file__).parent
observations = []


def snapshot(app, name):
    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    tree = ET.fromstring(app.export_screenshot(title='ignovate · interaction audit'))
    for node in tree.iter():
        if node.tag.endswith('}text') and node.text and node.get('textLength'):
            node.set('textLength', str(float(node.get('textLength')) * cell_len(node.text) / len(node.text)))
            node.set('lengthAdjust', 'spacingAndGlyphs')
    OUTPUT.joinpath(name + '.svg').write_text(ET.tostring(tree, encoding='unicode'), encoding='utf-8')


async def model_scene(size, count):
    with tempfile.TemporaryDirectory() as root:
        settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-flash', Path(root))
        app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None), settings)
        async with app.run_test(size=size) as pilot:
            app._append_user('项目检查')
            app._append_assistant('模型选择过程中应该仍然可以阅读这段已有对话。')
            prefs = {'model_name': 'default', 'model': 'deepseek-flash', 'models': {
                'default': {'model': 'deepseek-flash'},
                **{f'model{i}': {'model': f'deepseek-pro-{i}'} for i in range(1, count)}
            }}
            screen = ModelScreen(prefs)
            await app.push_screen(screen)
            await pilot.pause()
            before = screen.query_one('#model-options', OptionList).highlighted
            await pilot.press('2')
            await pilot.pause()
            after = screen.query_one('#model-options', OptionList).highlighted
            keys = screen.query_one('#model-keys')
            dialog = screen.query_one('#model-dialog')
            observations.append({'scene': f'model-{count}-{size}', 'screen_stack': len(app.screen_stack),
                'numeric_key_2_before': before, 'numeric_key_2_after': after,
                'keys_region': list(keys.region), 'dialog_content_region': list(dialog.content_region),
                'button_regions': {name:list(screen.query_one('#'+name).region)
                    for name in ('model-add','model-use','model-cancel')}})
            snapshot(app, f'model-{count}-{size[0]}x{size[1]}')
            try:
                observations[-1]['add_button_click'] = await pilot.click('#model-add')
            except Exception as error:
                observations[-1]['add_button_click_error'] = type(error).__name__
            if not screen.query_one('#model-dialog').has_class('adding'):
                observations[-1]['form_opened_programmatically'] = True
                screen.show_form()  # Inspect form state separately from the unreachable menu action.
            await pilot.pause()
            screen.query_one('#model-name', Input).value = 'draft-alias'
            screen.query_one('#model-id', Input).value = 'draft-model'
            snapshot(app, f'model-form-{size[0]}x{size[1]}')
            await pilot.press('escape')
            await pilot.pause()
            observations[-1]['escape_from_add'] = {
                'returns_to_model_list': isinstance(app.screen, ModelScreen),
                'focused': app.focused.id if app.focused else None,
                'screen_stack': len(app.screen_stack)}


async def welcome_scene():
    with tempfile.TemporaryDirectory() as root:
        settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-flash', Path(root),
                            cli_preferences={'theme': 'light'})
        store = BootstrapStore(Path(root) / 'bootstrap')
        app = SetupApp(store=store, settings=settings)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            snapshot(app, 'setup-light-request-80x24')
            observations.append({'scene': 'setup-light-request',
                'requested_theme': settings.cli_preferences['theme'],
                'actual_screen_background': str(app.screen.styles.background)})
            await pilot.press('enter')
            app.query_one('#setup-model', Input).value = 'deepseek-v4-pro'
            app.query_one('#setup-key', Input).value = 'fixture-key'
            await pilot.click('#setup-next')
            await pilot.pause()
            snapshot(app, 'setup-reasoning-80x24')
            await pilot.press('enter')
            await pilot.pause()
            snapshot(app, 'setup-permissions-80x24')
            await pilot.press('escape', 'escape')
            await pilot.pause()
            observations[-1]['back_to_connection'] = {
                'step': app.step,
                'model_draft_preserved': app.query_one('#setup-model', Input).value == 'deepseek-v4-pro',
                'key_draft_preserved': app.query_one('#setup-key', Input).value == 'fixture-key',
                'config_written': store.path.exists()}


async def main():
    await model_scene((80, 24), 14)
    await model_scene((60, 18), 2)
    await welcome_scene()
    OUTPUT.joinpath('interaction-observations.json').write_text(
        json.dumps(observations, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(observations, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
