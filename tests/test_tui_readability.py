"""Direct transcript selection, compact model choices and clear usage labels."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import Settings
from tui import TerminalAgentApp
from textual.widgets import RichLog, TextArea
from textual.geometry import Offset
from textual.selection import Selection
from textual.strip import Strip
from rich.segment import Segment
from rich.cells import cell_len
from ui.presentation import SessionMetrics, render_composer_metrics
from ui.token_weather import render_token_weather
from ui.theme import Theme
from ui.transcript import TranscriptLog
from agent_service import TurnEvent


class DirectSelectionTests(unittest.IsolatedAsyncioTestCase):
    def app(self, root):
        return TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None),
            Settings('private-key', 'https://api.invalid', 'deepseek-chat', Path(root)))

    async def drag(self, pilot, log, text):
        for y in range(log.scrollable_content_region.height):
            line = log.render_line(y).text
            if text in line:
                prefix = cell_len(line[:line.index(text)])
                origin = (log.gutter.left + prefix, log.gutter.top + y)
                await pilot.mouse_down('#transcript', offset=origin)
                # Screen selections include the character under the end cell.
                end = (origin[0] + cell_len(text[:-1]), origin[1])
                await pilot.hover('#transcript', offset=end)
                await pilot.mouse_up('#transcript', offset=end)
                await pilot.pause()
                return
        self.fail(f'{text!r} is not visible in the transcript')

    async def test_select_arbitrary_chinese_and_english_fields_in_transcript(self):
        with tempfile.TemporaryDirectory() as root, patch('shutil.which', return_value=None):
            for size in [(100,32), (80,24)]:
                app = self.app(root)
                async with app.run_test(size=size) as pilot:
                    app._append_assistant('这是可直接选取的中文字段，编号 ABC-123。\n\n第二段正文。')
                    app.query_one('#composer', TextArea).text = '保留输入'
                    await pilot.pause()
                    log = app.query_one('#transcript', RichLog)
                    await self.drag(pilot, log, '中文字段')
                    visible = [segment for y in range(log.size.height) for segment in log.render_line(y)]
                    selected = next(segment for segment in visible if segment.text == '中文字段')
                    surrounding = next(segment for segment in visible if '这是可直接选取的' in segment.text)
                    self.assertNotEqual(selected.style.bgcolor, surrounding.style.bgcolor,
                                        '选区必须有可见高亮，不能只在内部记录坐标')
                    await pilot.press('ctrl+c')
                    await pilot.pause()
                    self.assertEqual(app.clipboard, '中文字段')
                    self.assertEqual(app.query_one('#composer', TextArea).text, '保留输入')
                    await pilot.press('escape')
                    await pilot.pause()
                    self.assertFalse(app.screen.selections)
                    await self.drag(pilot, log, 'ABC-123')
                    await pilot.press('ctrl+c')
                    await pilot.pause()
                    self.assertEqual(app.clipboard, 'ABC-123')
                    self.assertFalse(app.query('#copy-reply'), '正文选择不应依赖一键复制按钮')

    async def test_stream_updates_do_not_change_the_text_under_selection(self):
        with tempfile.TemporaryDirectory() as root, patch('shutil.which', return_value=None):
            app = self.app(root)
            async with app.run_test(size=(80,24)) as pilot:
                app._update_live_response('选中的文字\n第二行')
                await pilot.pause()
                log = app.query_one('#transcript', RichLog)
                await self.drag(pilot, log, '选中的文字')
                app._update_live_response('更新后的文字\n第三行')
                await pilot.pause()
                await pilot.press('ctrl+c')
                await pilot.pause()
                self.assertEqual(app.clipboard, '选中的文字')
                await pilot.press('escape')
                await pilot.pause()
                self.assertIn('更新后的文字', '\n'.join(line.text for line in log.lines))

    async def test_model_choice_uses_only_the_space_needed_for_options(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            async with app.run_test(size=(100,32)) as pilot:
                app._append_assistant('上方对话应保持可见。')
                app._dispatch('/model')
                await pilot.pause()
                dialog = app.screen.query_one('#model-dialog')
                options = app.screen.query_one('#model-options')
                self.assertLessEqual(options.size.height, 4, '单个模型不应占满窗口')
                self.assertLessEqual(dialog.size.height, 13)
                self.assertIn(dialog.styles.border_left[0], ('', 'none'))
                await pilot.press('escape')
                await app.session_runner.wait_idle()

    async def test_blank_transcript_area_can_be_selected_without_stopping_or_crashing(self):
        with tempfile.TemporaryDirectory() as root, patch('shutil.which', return_value=None):
            app = self.app(root)
            async with app.run_test(size=(100,32)) as pilot:
                app._append_user('短问题')
                app._append_assistant('短回复')
                app.query_one('#composer', TextArea).text = '保留输入'
                await pilot.pause()
                log = app.query_one('#transcript', RichLog)
                blank = (log.gutter.left + 2, log.gutter.top + len(log.lines) + 1)
                await pilot.mouse_down('#transcript', offset=blank)
                await pilot.hover('#transcript', offset=(blank[0] + 4, blank[1]))
                await pilot.mouse_up('#transcript', offset=(blank[0] + 4, blank[1]))
                await pilot.press('ctrl+c')
                await pilot.pause()
                self.assertEqual(app.query_one('#composer', TextArea).text, '保留输入')

    async def test_direct_selection_does_not_cancel_an_actual_running_turn(self):
        release, started = asyncio.Event(), asyncio.Event()
        class Service:
            runtime_factory = None
            session_store = None
            async def stream_turn(self, _message, _config, **_kwargs):
                yield TurnEvent('token', {'text':'生成中的正文 ABC-123'})
                started.set()
                await release.wait()
                yield TurnEvent('final', {'text':'最终回复 ABC-123'})
        with tempfile.TemporaryDirectory() as root, patch('shutil.which', return_value=None):
            app = TerminalAgentApp(Service(), Settings('key', 'https://api.invalid', 'model', Path(root)))
            async with app.run_test(size=(80,24)) as pilot:
                app._dispatch('启动任务')
                await asyncio.wait_for(started.wait(), 3)
                try:
                    log = app.query_one('#transcript', RichLog)
                    await pilot.pause()
                    await self.drag(pilot, log, 'ABC-123')
                    await pilot.press('ctrl+c')
                    await pilot.pause()
                    self.assertEqual(app.clipboard, 'ABC-123')
                    self.assertTrue(app.session_runner.busy)
                    release.set()
                    await app.session_runner.wait_idle()
                    await pilot.pause()
                    self.assertIn('生成中的正文', '\n'.join(line.text for line in log.lines))
                    await pilot.press('escape')
                    await pilot.pause()
                    self.assertIn('最终回复', '\n'.join(line.text for line in log.lines))
                    self.assertEqual(app.completed_turns, 1)
                finally:
                    release.set()
                    await app.session_runner.wait_idle()


class UsageLabelTests(unittest.TestCase):
    def test_selection_in_a_trailing_empty_rendered_line_is_empty(self):
        log = TranscriptLog()
        log.lines = [Strip([Segment('正文')]), Strip([Segment('')])]
        self.assertEqual(log.get_selection(Selection(Offset(0,1), Offset(1,1))), ('', '\n'))

    def test_missing_usage_has_words_instead_of_question_mark_suffixes(self):
        metrics = SessionMetrics.from_events([
            {'kind':'turn_start'}, {'kind':'usage_missing','data':{'scope':'main'}},
            {'kind':'turn_start'}, {'kind':'usage','data':{'input_tokens':19800,'output_tokens':10}},
        ])
        stats = render_composer_metrics(metrics, model='deepseek-flash', context_window=1000000,
                                       width=100, compact=True, include_context=False).plain
        weather = render_token_weather(metrics, context_window=1000000, width=100,
                                       theme=Theme(no_color=True)).plain
        self.assertIn('用量不完整', stats)
        self.assertNotIn('+?', stats)
        self.assertNotIn('?', weather)
        self.assertIn('增量待统计', weather)
        self.assertIn('19.8k / 1m', weather)
