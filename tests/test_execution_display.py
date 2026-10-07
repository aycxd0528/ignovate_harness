"""Regressions for readable execution records across the terminal interfaces."""

import asyncio
import os
from io import StringIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from langchain_core.messages import ToolMessage
from rich.cells import cell_len
from rich.console import Console as RichConsole
from rich.style import Style
from textual.widgets import RichLog, Static

from agent_service import AgentService, TurnEvent
from config import Settings
from tui import TerminalAgentApp
from ui.console import Console
from ui.plain import PlainConsole
from ui.presentation import StepTracker, render_role_header, render_tool_line
from ui.render import render_user_message
from ui.theme import Theme, load_theme


class ExecutionDisplayTests(unittest.TestCase):
    def test_plain_console_preserves_ascii_markers_when_theme_changes(self):
        console = PlainConsole()
        for name in ("dark", "light", "ansi"):
            console.apply_theme(load_theme(name=name))
            self.assertTrue(render_user_message("hello", theme=console.theme).plain.startswith("> "))
            self.assertTrue(render_role_header("assistant", theme=console.theme).plain.startswith("* "))

    def test_command_result_keeps_exit_status_and_failure_at_narrow_widths(self):
        for width in (32, 48, 80):
            with self.subTest(width=width):
                row = render_tool_line({
                    "name": "run_command", "ok": False, "exit_code": 2,
                    "preview": {"command": "python -m unittest " + "很长的参数" * 20},
                    "summary": "测试失败", "elapsed_ms": 880,
                }, width=width, theme=Theme(no_color=True))
                self.assertIn("退出码 2", row.plain)
                self.assertIn("失败", row.plain)
                self.assertLessEqual(cell_len(row.plain), width)

    def test_tracker_pairs_command_with_output_and_timeout(self):
        tracker = StepTracker(api_key="sentinel-secret")
        tracker.observe(TurnEvent("tool_start", {
            "call_id": "cmd", "name": "run_command",
            "preview": {"command": "python check.py sentinel-secret"},
        }))
        rows = tracker.observe(TurnEvent("tool_end", {
            "call_id": "cmd", "name": "run_command", "ok": False,
            "exit_code": -1, "timed_out": True, "summary": "命令执行超时",
            "output_snippet": "trace sentinel-secret", "elapsed_ms": 30000,
        }))
        display = "\n".join(row.plain for row in rows)
        self.assertIn("python check.py", display)
        self.assertIn("超时", display)
        self.assertIn("退出码 -1", display)
        self.assertIn("trace", display)
        self.assertNotIn("sentinel-secret", display)

    def test_normal_inline_shows_execution_without_debug_bookkeeping(self):
        output = StringIO()
        console = Console(theme=Theme(no_color=True), console=RichConsole(file=output, width=80, no_color=True))
        with console.working():
            console.print_event(TurnEvent("usage", {"input_tokens": 100, "output_tokens": 5}))
            console.print_event(TurnEvent("tool_start", {
                "call_id": "cmd", "name": "run_command", "preview": {"command": "python -m unittest"},
            }))
            console.print_event(TurnEvent("tool_end", {
                "call_id": "cmd", "name": "run_command", "ok": True,
                "exit_code": 0, "output_snippet": "Ran 12 tests\nOK", "elapsed_ms": 800,
            }))
            console.print_event(TurnEvent("final", {"text": "检查完成"}))
        display = output.getvalue()
        self.assertIn("python -m unittest", display)
        self.assertIn("退出码 0", display)
        self.assertNotIn("Ran 12 tests", display)
        self.assertEqual(display.count("检查完成"), 1)
        self.assertNotIn("step 1", display)
        self.assertNotIn("tokens im=", display)
        self.assertNotIn("completed", display)

    def test_short_and_failed_command_outputs_remain_available(self):
        service = AgentService(lambda **kwargs: None, api_key="sentinel-secret")
        for payload in (
            '{"ok": true, "exit_code": 0, "output": "OK\\n"}',
            '{"ok": false, "exit_code": 1, "error": "测试失败", "output": "trace sentinel-secret\\n"}',
        ):
            with self.subTest(payload=payload):
                result = service._tool_result_summary(ToolMessage(content=payload, name="run_command", tool_call_id="cmd"))
                self.assertTrue(result.get("output_snippet"))
                self.assertNotIn("sentinel-secret", str(result))

    def test_final_reply_after_tool_commentary_is_not_lost(self):
        output = StringIO()
        console = Console(theme=Theme(no_color=True), console=RichConsole(file=output, width=80, no_color=True))
        with console.working():
            console.print_event(TurnEvent('token', {'text': '先读取文件。'}))
            console.print_event(TurnEvent('tool_start', {'call_id': 'r', 'name': 'read_file', 'preview': {'path': 'a.py'}}))
            console.print_event(TurnEvent('tool_end', {'call_id': 'r', 'name': 'read_file', 'ok': True}))
            console.print_event(TurnEvent('final', {'text': '文件检查完成。'}))
        display = output.getvalue()
        self.assertEqual(display.count('先读取文件。'), 1)
        self.assertEqual(display.count('文件检查完成。'), 1)
        self.assertLess(display.index('先读取文件。'), display.index('a.py'))

    def test_output_is_redacted_before_preview_truncation(self):
        key = 'sentinel-boundary-secret'
        output = key + 'x' * 5993
        service = AgentService(lambda **kwargs: None, api_key=key)
        import json
        result = service._tool_result_summary(ToolMessage(content=json.dumps({'ok': True, 'output': output, 'exit_code': 0}), name='run_command', tool_call_id='r'))
        self.assertNotIn('secret', result.get('output_preview', ''))
        self.assertTrue(result['output_truncated'])
        self.assertLessEqual(len(result['output_preview']), 6002)

    def test_command_is_redacted_before_preview_truncation(self):
        key = 'sentinel-boundary-secret'
        service = AgentService(lambda **kwargs: None, api_key=key)
        result = service._tool_start_data('run_command', {'command': 'x' * 1990 + key}, 'r')
        self.assertNotIn('sentinel', result['preview']['command'])


class ExecutionDashboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_shows_step_statistics_only_in_detailed_mode(self):
        for output_style in ('normal', 'detailed'):
            with self.subTest(output_style=output_style), tempfile.TemporaryDirectory() as directory:
                app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None), Settings('key', 'https://api.invalid', 'deepseek-chat', Path(directory)))
                async with app.run_test(size=(80, 24)) as pilot:
                    app.actions.preferences.set('output_style', output_style)
                    tracker, streamed = StepTracker(), []
                    app._render_flow_event(TurnEvent('usage', {'input_tokens': 50, 'output_tokens': 2}), tracker, streamed)
                    app._render_flow_event(TurnEvent('final', {'text': '检查完成'}), tracker, streamed)
                    await pilot.pause()
                    display = ''.join(line.text for line in app.query_one('#transcript', RichLog).lines)
                    self.assertEqual('模型调用 1' in display, output_style == 'detailed')
                    self.assertEqual('tokens im=50 out=2' in display, output_style == 'detailed')
                    self.assertEqual('完成 · 1 次模型调用' in display, output_style == 'detailed')

    async def test_resizing_keeps_newest_messages_visible_when_following_the_bottom(self):
        with tempfile.TemporaryDirectory() as directory:
            app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None), Settings('key', 'https://api.invalid', 'deepseek-chat', Path(directory)))
            async with app.run_test(size=(100, 30)) as pilot:
                app._append_user('检查项目')
                for index in range(15):
                    app._append_assistant(f"Paragraph {index:02d}: " + "需要在窄窗口中重新换行并保持滚动位置。" * 4)
                await pilot.pause()
                log = app.query_one('#transcript', RichLog)
                self.assertTrue(log.is_vertical_scroll_end)
                await pilot.resize_terminal(40, 18)
                await pilot.pause()
                self.assertTrue(log.is_vertical_scroll_end)
                visible = ''.join(log.render_line(y).text for y in range(log.scrollable_content_region.height))
                self.assertIn('Paragraph 14', visible)
                log.scroll_to(y=0, animate=False)
                await pilot.pause()
                await pilot.resize_terminal(90, 28)
                await pilot.pause()
                self.assertEqual(log.scroll_y, 0, '阅读较早的消息时不应强制跳到末尾')

    async def test_theme_switch_recolors_messages_already_in_history(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            os.environ.pop('NO_COLOR', None)
            app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None), Settings('key', 'https://api.invalid', 'deepseek-chat', Path(directory)))
            async with app.run_test(size=(80, 24)) as pilot:
                app._append_user('检查主题')
                app._append_assistant('已有回复也应该保持可读。')
                await pilot.pause()
                app.actions.preferences.set('theme', 'light')
                app._refresh_preferences()
                await pilot.pause()
                segments = [segment for line in app.query_one('#transcript', RichLog).lines for segment in line if '已有回复' in segment.text]
                self.assertTrue(segments)
                self.assertEqual(segments[0].style.color, Style(color='#28343c').color)

    async def test_new_reply_remains_visible_after_activity_and_theme_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None), Settings('key', 'https://api.invalid', 'deepseek-chat', Path(directory)))
            async with app.run_test(size=(100, 30)) as pilot:
                app._append_user('检查项目')
                for index in range(8):
                    app._append_assistant(f'回复 {index}：' + '已有对话内容。' * 10)
                await pilot.pause()
                app._append_activity(TurnEvent('tool_start', {'call_id': 'run', 'name': 'run_command', 'preview': {'command': 'python -m unittest'}}), [])
                await pilot.pause()
                app._clear_tool_activity()
                app.actions.preferences.set('theme', 'light')
                app._refresh_preferences()
                app._append_user('检查新回复')
                app._append_assistant('最新回复应在视口中显示。')
                await pilot.pause()
                log = app.query_one('#transcript', RichLog)
                visible = ''.join(log.render_line(y).text for y in range(log.scrollable_content_region.height))
                self.assertIn('最新回复应在视口中显示。', visible)

    async def test_resizing_reflows_existing_transcript_without_losing_the_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            service = SimpleNamespace(runtime_factory=None, session_store=None)
            app = TerminalAgentApp(service, Settings('key', 'https://api.invalid', 'deepseek-chat', Path(directory)))
            async with app.run_test(size=(100, 30)) as pilot:
                app._append_user('检查项目')
                app._append_assistant('这是一段较长的中文回复，需要在窗口变窄时重新换行，保留最后的结论。')
                await pilot.pause()
                await pilot.resize_terminal(40, 18)
                await pilot.pause()
                log = app.query_one('#transcript', RichLog)
                self.assertLessEqual(log.virtual_size.width, log.scrollable_content_region.width)
                self.assertIn('最后的结论。', ''.join(line.text for line in log.lines))

    async def test_tool_progress_is_visible_and_result_arrives_before_final_reply(self):
        started, finish_tool, finished, finish_reply = (asyncio.Event() for _ in range(4))

        class Service:
            runtime_factory = None
            session_store = None

            async def stream_turn(self, message, config, **kwargs):
                yield TurnEvent("tool_start", {
                    "call_id": "cmd", "name": "run_command",
                    "preview": {"command": "python -m unittest", "cwd": "/tmp/project"},
                })
                started.set()
                await finish_tool.wait()
                yield TurnEvent("tool_end", {
                    "call_id": "cmd", "name": "run_command", "ok": True,
                    "exit_code": 0, "output_snippet": "Ran 12 tests\nOK", "elapsed_ms": 800,
                })
                finished.set()
                await finish_reply.wait()
                yield TurnEvent("final", {"text": "检查完成"})

        with tempfile.TemporaryDirectory() as directory:
            app = TerminalAgentApp(Service(), Settings("secret-key", "https://api.invalid", "deepseek-chat", Path(directory)))
            async with app.run_test(size=(80, 24)) as pilot:
                app._dispatch("检查并测试")
                try:
                    await asyncio.wait_for(started.wait(), 2)
                    await pilot.pause()
                    transcript = "\n".join(strip.text for strip in app.query_one("#transcript", RichLog).lines)
                    self.assertIn("python -m unittest", transcript,
                                  "正在执行的工具应在对话中有可见记录")
                    self.assertNotIn("退出码", transcript)
                    finish_tool.set()
                    await asyncio.wait_for(finished.wait(), 2)
                    await pilot.pause()
                    transcript = "\n".join(strip.text for strip in app.query_one("#transcript", RichLog).lines)
                    self.assertIn("退出码 0", transcript)
                    self.assertNotIn("Ran 12 tests", transcript)
                    await pilot.press('ctrl+o')
                    await pilot.pause()
                    transcript = "\n".join(strip.text for strip in app.query_one("#transcript", RichLog).lines)
                    self.assertIn("Ran 12 tests", transcript)
                    self.assertNotIn("检查完成", transcript)
                    self.assertIsNone(app._live_response.pending_activity)
                    conversation = app.screen
                    app._show_tool_details(1)
                    await pilot.pause()
                    self.assertIs(app.screen, conversation, '工具详情应在正文展开')
                    text = '\n'.join(line.text for line in app.query_one('#transcript', RichLog).lines)
                    self.assertIn("python -m unittest", text)
                    self.assertIn("退出码", text)
                    self.assertNotIn('"name":', text)
                finally:
                    finish_tool.set()
                    finish_reply.set()
                    await app.session_runner.wait_idle()
                await pilot.pause()
                self.assertIs(app.screen, conversation)
                self.assertFalse(app.query('#tool-details-dialog'))
                transcript = "\n".join(strip.text for strip in app.query_one("#transcript", RichLog).lines)
                self.assertEqual(transcript.count("检查完成"), 1)


if __name__ == "__main__":
    unittest.main()
