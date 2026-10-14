"""Export final native compositor cells for readable, offline PNG previews.

Run with project Python; render the JSON with render_cells.py and bundled Pillow.
The compositor is authoritative; the PNG is a fixed-cell font visualization.
"""
import asyncio
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

from config import Settings
from nailong.core.bootstrap import BootstrapStore
from tui import TerminalAgentApp
from ui.help_view import HelpScreen
from ui.model_view import ModelScreen
from ui.setup import SetupApp
from ui.theme import load_theme
from rich.cells import get_character_cell_size
from textual.widgets import Input

OUT = Path(__file__).resolve().parent
scenes = []


def capture(app, name):
    theme = app._ui_theme
    lines = []
    for strip in app.screen._compositor.render_strips():
        cells = []
        for segment in strip:
            if segment.control:
                continue
            style = segment.style
            foreground = theme.foreground
            background = theme.background
            if style is not None:
                if style.color is not None and not style.color.is_default:
                    foreground = style.color.get_truecolor().hex
                if style.bgcolor is not None and not style.bgcolor.is_default:
                    background = style.bgcolor.get_truecolor().hex
                if style.reverse:
                    foreground, background = background, foreground
            for character in segment.text:
                cells.append({'text': character, 'width': get_character_cell_size(character),
                              'fg': foreground, 'bg': background,
                              'bold': bool(style and style.bold),
                              'underline': bool(style and style.underline)})
        lines.append(cells)
    scenes.append({'name': name, 'size': list(app.size), 'background': theme.background,
                   'lines': lines, 'screen_stack': len(app.screen_stack)})


async def main():
    os.environ.pop('NO_COLOR', None)
    for name, size in [('dark', (100, 32)), ('light', (60, 18))]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'LLM_project'
            root.mkdir()
            app = SetupApp(store=BootstrapStore(Path(directory) / 'user'),
                           project_root=root, theme=load_theme(name=name))
            async with app.run_test(size=size) as pilot:
                await pilot.pause()
                capture(app, f'preview-welcome-{name}-{size[0]}x{size[1]}')
                if name == 'light':
                    await pilot.press('enter')
                    app.query_one('#setup-base', Input).value = 'invalid'
                    app.next_step()
                    await pilot.pause()
                    capture(app, 'preview-setup-error-light-60x18')
    for name, size in [('dark', (100, 32)), ('dark', (80, 24)),
                       ('dark', (60, 18)), ('light', (80, 24))]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'LLM_project'
            root.mkdir()
            settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-flash', root,
                                reasoning_effort='max', cli_preferences={'theme': name})
            app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None), settings)
            async with app.run_test(size=size) as pilot:
                await pilot.pause()
                if size == (100, 32):
                    capture(app, 'preview-conversation-empty-dark-100x32')
                app._append_user('介绍这个项目')
                app._append_assistant('项目入口是 `main.py`。\n\n可以读代码、检查测试和执行任务。')
                preferences = {'model_name': 'default', 'model': 'deepseek-flash', 'models': {
                    'default': {'model': 'deepseek-flash'},
                    **{f'model-{i}': {'model': f'deepseek-model-{i}'} for i in range(13)}}}
                panel = ModelScreen(preferences)
                task = asyncio.create_task(app._wait_panel(panel))
                await pilot.pause()
                capture(app, f'preview-models-14-{name}-{size[0]}x{size[1]}')
                if size == (60, 18):
                    panel.show_form()
                    await pilot.pause()
                    capture(app, 'preview-model-form-dark-60x18')
                panel.complete(None)
                await task
                if size == (80, 24):
                    panel = HelpScreen(app.controller.specs(), app._ui_theme, initial_tab='commands')
                    task = asyncio.create_task(app._wait_panel(panel))
                    await pilot.pause()
                    capture(app, f'preview-help-{name}-80x24')
                    panel.complete(None)
                    await task
                if size == (60, 18):
                    task = asyncio.create_task(app._request_approval(
                        {'name': 'run_command', 'args': {'command': 'python -m unittest', 'timeout_seconds': 15},
                         '_approval': {'reason': '执行项目测试', 'preview': {'cwd': str(root)}}}, 1, 1))
                    await pilot.pause()
                    capture(app, 'preview-approval-dark-60x18')
                    app._approval_future.set_result('reject')
                    await task
    (OUT / 'preview-cells.json').write_text(json.dumps(scenes, ensure_ascii=False, indent=2))
    print(f'Exported {len(scenes)} native compositor previews; no model requests.')


if __name__ == '__main__':
    asyncio.run(main())
