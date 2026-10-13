"""Render temporary Textual conversations; no API or shell tool is invoked."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import xml.etree.ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from agent_service import TurnEvent
from config import Settings
from tui import TerminalAgentApp
from ui.presentation import StepTracker
from rich.cells import cell_len


def save_preview(app, name, directory):
    app.save_screenshot(name, path=str(directory))
    path = directory / name
    tree = ET.parse(path)
    # Rich's SVG export measures textLength by codepoints while terminal x
    # positions use display cells. Keep CJK at its real two-cell width in the
    # standalone preview; this does not change the Textual/terminal renderer.
    for element in tree.iter():
        text = element.text or ''
        if element.tag.endswith('}text') and text and element.get('textLength'):
            element.set('textLength', str(float(element.get('textLength')) * cell_len(text) / len(text)))
            element.set('lengthAdjust', 'spacingAndGlyphs')
    tree.write(path, encoding='utf-8', xml_declaration=True)


async def main():
    previews = REPO_ROOT / 'docs/previews'
    measurements = []
    with patch.dict(os.environ):
        os.environ.pop('NO_COLOR', None)
        for width, height in ((100, 32), (80, 24)):
            with tempfile.TemporaryDirectory() as directory:
                settings = Settings('fake-smoke-key', 'https://api.invalid', 'deepseek-flash', Path(directory))
                app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None), settings, thread_id='ui-smoke')
                async with app.run_test(size=(width, height)) as pilot:
                    app._append_user('修复加法函数，并确认运行结果')
                    tracker, streamed = StepTracker(), []
                    app._render_flow_event(TurnEvent('usage', {'input_tokens': 1240, 'output_tokens': 62,
                        'cache_hit_tokens': 1024, 'scope': 'main'}), tracker, streamed)
                    for index, path in enumerate(('calc.py', 'check.py')):
                        call_id = f'call-{index}'
                        app._render_flow_event(TurnEvent('tool_start', {'call_id': call_id,
                            'name': 'read_file', 'preview': {'path': path}}), tracker, streamed)
                        app._render_flow_event(TurnEvent('tool_end', {'call_id': call_id,
                            'name': 'read_file', 'path': path, 'ok': True, 'elapsed_ms': 4}), tracker, streamed)
                    app._append_assistant('已定位 `add` 中的减法运算。\n\n准备把 `a - b` 替换为 `a + b`；批准后执行。')
                    await pilot.pause()
                    save_preview(app, f'runtime-conversation-{width}x{height}.svg', previews)
                    pending = asyncio.create_task(app._request_approval({'name': 'edit_file',
                        'args': {'path': 'calc.py', 'old_string': 'return a - b', 'new_string': 'return a + b'},
                        '_approval': {'reason': '修改项目文件，需要你的允许。',
                            'suggested_rule': 'Edit(./calc.py)', 'preview': {'path': 'calc.py',
                                'diff': '--- calc.py\n+++ calc.py\n-    return a - b\n+    return a + b'}}}, 1, 1))
                    try:
                        await pilot.pause()
                        for expanded in (False, True):
                            if expanded:
                                await pilot.press('d')
                                await pilot.pause()
                            save_preview(app, f'runtime-approval-{width}x{height}-{"expanded" if expanded else "compact"}.svg', previews)
                            panel = app.query_one('#approval-panel')
                            composer = app.query_one('#composer-frame')
                            metrics = app.query_one('#composer-stats')
                            measurements.append({'size': [width, height], 'expanded': expanded,
                                'approval_height': panel.size.height, 'approval_bottom': panel.region.bottom,
                                'composer_top': composer.region.y, 'metrics_bottom': metrics.region.bottom,
                                'choices_on_screen': panel.query_one('#approval-choices').region.bottom <= panel.region.bottom,
                                'hint_on_screen': panel.query_one('#approval-hint').region.bottom <= panel.region.bottom,
                                'no_overlap': panel.region.bottom <= composer.region.y and metrics.region.bottom <= height})
                        await pilot.press('escape')
                        assert await asyncio.wait_for(pending, 3) == 'reject'
                    finally:
                        if not pending.done():
                            pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
    print(json.dumps({'all_passed': all(row['choices_on_screen'] and row['hint_on_screen'] and row['no_overlap']
        for row in measurements), 'measurements': measurements}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
