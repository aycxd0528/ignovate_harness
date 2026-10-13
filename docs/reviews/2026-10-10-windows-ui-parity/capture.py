"""Offline review of the shared Textual UI; never calls a model or saves setup."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from agent_service import TurnEvent
from config import Settings
from nailong.core.bootstrap import BootstrapStore
from rich.cells import cell_len
from tui import TerminalAgentApp
from ui.setup import SetupApp
from ui.theme import load_theme

OUT = Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)
observations = []
previews = []

def color(value, default):
    return value.get_truecolor().hex if value is not None else default

def capture(app, scene, theme, size, selectors):
    name = f'{scene}-{theme.name}-{size[0]}x{size[1]}'
    (OUT / (name + '.svg')).write_text(app.export_screenshot(title='ignovate v1.0.3'))
    row = {'name': name, 'size': size, 'theme': theme.name,
           'color_system': app.console.color_system, 'widgets': {}}
    for selector in selectors:
        w = app.query_one(selector)
        r = w.region
        row['widgets'][selector] = {'region': [r.x, r.y, r.width, r.height],
                                   'background': w.styles.background.hex,
                                   'foreground': w.styles.color.hex}
        assert 0 <= r.x <= r.right <= size[0], (name, selector, r)
        assert 0 <= r.y <= r.bottom <= size[1], (name, selector, r)
    if scene in ('composer-empty', 'composer-active'):
        weather = app.query_one('#token-weather')
        stats = app.query_one('#composer-stats')
        assert weather.region.y == stats.region.y
        assert weather.region.right <= stats.region.x
        assert stats.region.right == app.query_one('#composer-info').content_region.right
        assert weather.region.bottom <= app.query_one('#composer-frame').region.y
        assert cell_len(weather.content.plain) <= weather.size.width
        row['weather'] = weather.content.plain
        row['weather_spans'] = [str(span.style) for span in weather.content.spans]
    if scene == 'welcome':
        row['logo'] = app.query_one('#setup-logo').render().plain
        assert all(c in row['logo'] for c in ('╔', '║', '═'))
    observations.append(row)
    if theme.name == 'dark' and size == (100, 32) and scene in ('welcome', 'composer-active', 'approval'):
        lines = []
        for strip in app.screen._compositor.render_strips():
            cells = []
            for segment in strip:
                style = segment.style
                for character in segment.text:
                    cells.append({'text': character, 'width': cell_len(character),
                        'fg': color(style.color if style else None, theme.foreground),
                        'bg': color(style.bgcolor if style else None, theme.background),
                        'bold': bool(style and style.bold), 'underline': bool(style and style.underline)})
            lines.append(cells)
        previews.append({'name': name, 'size': size, 'background': theme.background, 'lines': lines})

async def ready(pilot, condition):
    async def wait():
        while True:
            await pilot.pause()
            if condition():
                return
    await asyncio.wait_for(wait(), 5)

async def main():
    with tempfile.TemporaryDirectory(prefix='ignovate-ui-review-') as directory:
        root = Path(directory) / 'sample-project'
        root.mkdir()
        os.environ['NAILONG_DATA_DIR'] = str(Path(directory) / 'data')
        os.environ['IGNOVATE_CONFIG_DIR'] = str(Path(directory) / 'config')
        os.environ.pop('NO_COLOR', None)
        os.environ.pop('LC_ALL', None)
        settings = Settings('review-fixture-key', 'https://example.invalid', 'deepseek-flash', root)
        for theme_name in ('dark', 'light'):
            theme = load_theme(name=theme_name)
            for size in ((80, 24), (100, 32), (213, 56)):
                store = BootstrapStore(Path(directory) / f'user-{theme_name}-{size[0]}')
                app = SetupApp(store=store, settings=settings, theme=theme)
                async with app.run_test(size=size) as pilot:
                    await pilot.pause()
                    for step, scene, focus in ((0, 'welcome', '#welcome-options'),
                                               (1, 'connection', '#setup-key'),
                                               (2, 'reasoning', '#setup-reasoning'),
                                               (3, 'permissions', '#setup-permissions')):
                        app.show_step(step)
                        await pilot.pause()
                        capture(app, scene, theme, size, ['#setup-composer', focus])
                        if step == 1:
                            assert app.query_one('#setup-key').password
                    await pilot.press('escape')
                assert not store.path.exists()
                app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None), settings)
                async with app.run_test(size=size) as pilot:
                    preferences = {**app.actions.preferences.effective(), 'theme': theme_name}
                    with patch.object(app.actions.preferences, 'effective', return_value=preferences):
                        app._refresh_preferences()
                    await pilot.pause()
                    selectors = ['#token-weather', '#composer-stats', '#composer-frame']
                    capture(app, 'composer-empty', theme, size, selectors)
                    for tokens in (11000, 20300):
                        app._append_user('审查用量示例')
                        app._observe_usage(TurnEvent('usage', {'input_tokens': tokens, 'output_tokens': 100}))
                    app._append_assistant('示例回复：已检查布局。\n\n```python\nprint("hello")\n```')
                    await pilot.pause()
                    capture(app, 'composer-active', theme, size, selectors)
                    composer = app.query_one('#composer')
                    composer.text = '/'
                    await ready(pilot, lambda: composer.suggestions_active)
                    capture(app, 'commands', theme, size, ['#command-menu', '#composer-frame'])
                    await pilot.press('escape')
                    composer.text = ''
                    help_worker = app._show_help()
                    await ready(pilot, lambda: app._interaction_panel is not None and app._interaction_panel.region.height > 0)
                    capture(app, 'help', theme, size, ['#interaction-host', '#composer-frame'])
                    await pilot.press('escape')
                    await help_worker.wait()
                    task = asyncio.create_task(app._request_approval(
                        {'name': 'write_file', 'args': {'path': 'hello.py', 'content': 'print("hello")'}}, 1, 1))
                    try:
                        await ready(pilot, lambda: app.query_one('#approval-panel').display and app.query_one('#approval-panel').region.height > 0)
                        capture(app, 'approval', theme, size, ['#approval-panel', '#composer-frame'])
                        await pilot.press('escape')
                        assert await asyncio.wait_for(task, 5) == 'reject'
                    finally:
                        if not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
        (OUT / 'observations.json').write_text(json.dumps(observations, ensure_ascii=False, indent=2))
        (OUT / 'preview-cells.json').write_text(json.dumps(previews, ensure_ascii=False))
    print(f'{len(observations)} scenes verified; no model calls or setup writes; {OUT}')

asyncio.run(main())
