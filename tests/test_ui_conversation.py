import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from textual.widgets import RichLog, Static
from agent_service import TurnEvent
from config import Settings
from tui import TerminalAgentApp, ChatInput
from ui.help_view import HelpScreen
from ui.commands import COMMANDS, CommandSpec


def transcript(app):
    return '\n'.join(row.text for row in app.query_one('#transcript', RichLog).lines)


class ConversationLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def wait_for_condition(self, pilot, condition, description):
        async def ready():
            while True:
                await pilot.pause()
                if condition(): return
        try:
            await asyncio.wait_for(ready(), 3)
        except TimeoutError:
            self.fail(f'Timed out waiting for {description}; '
                      f'focus={getattr(pilot.app.focused,"id",None)!r}')

    async def test_help_is_local_and_returns_command_to_composer(self):
        with tempfile.TemporaryDirectory() as directory:
            app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None),
                Settings('secret', 'https://api.invalid', 'deepseek-chat', Path(directory)))
            app.controller.specs = lambda: COMMANDS + (CommandSpec('$audit', '长说明 ' * 1000, needs_argument=True),)
            async with app.run_test(size=(80, 24)) as pilot:
                app._append_user('已有对话')
                before = transcript(app)
                app._dispatch('/help')
                await self.wait_for_condition(pilot,
                    lambda: app.focused is not None and app.focused.id=='help-tabs',
                    'help tabs to mount and focus')
                self.assertIsInstance(app._interaction_panel, HelpScreen)
                self.assertEqual(transcript(app), before)
                self.assertNotIn('长说明', app.screen.query_one('#help-general', Static).content.plain)
                await pilot.press('escape')
                await self.wait_for_condition(pilot,
                    lambda: app._interaction_panel is None and app.focused is not None
                        and app.focused.id=='composer',
                    'help to close and restore composer focus')
                self.assertEqual(app.focused.id, 'composer')
                app._dispatch('/help skills')
                await self.wait_for_condition(pilot,
                    lambda: app.focused is not None and app.focused.id=='help-options'
                        and app.focused.content_region.height>0,
                    'skill choices to mount and focus')
                await pilot.press('ctrl+enter')
                await self.wait_for_condition(pilot,
                    lambda: app._interaction_panel is None
                        and app.query_one('#composer',ChatInput).text=='$audit ',
                    'selected skill to populate the composer')
                self.assertEqual(app.query_one('#composer', ChatInput).text, '$audit ')
                self.assertEqual(app.session_runner.queue, [])
                self.assertEqual(transcript(app), before)

    async def test_tool_phase_collapses_in_place_and_toggle_preserves_command(self):
        with tempfile.TemporaryDirectory() as directory:
            app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None),
                Settings('secret', 'https://api.invalid', 'deepseek-chat', Path(directory)))
            async with app.run_test(size=(80, 24)) as pilot:
                app._append_user('检查项目')
                for i in range(12):
                    app._append_activity(TurnEvent('tool_start', {'name': 'read_file', 'call_id': str(i), 'path': f'file{i}.py'}), [])
                    app._append_activity(TurnEvent('tool_end', {'name': 'read_file', 'call_id': str(i), 'ok': True, 'elapsed_ms': 10}), [])
                app._append_activity(TurnEvent('tool_end', {'name': 'run_command', 'call_id': 'test', 'ok': True,
                    'preview': {'command': 'python check.py secret'}, 'exit_code': 0, 'output_snippet': 'Ran 12 tests\nOK'}), [])
                await pilot.pause()
                self.assertEqual(len(app._tool_groups), 1)
                self.assertIn('读取 12', transcript(app))
                self.assertNotIn('file11.py', transcript(app))
                self.assertLess(len(app.query_one('#transcript', RichLog).lines), 5)
                await pilot.press('ctrl+o')
                await pilot.pause()
                self.assertIn('file11.py', transcript(app))
                self.assertIn('Ran 12 tests', transcript(app))
                self.assertNotIn('secret', transcript(app))
                await pilot.resize_terminal(100, 32)
                await pilot.pause()
                await pilot.press('ctrl+o')
                self.assertNotIn('Ran 12 tests', transcript(app))

    async def test_approval_keeps_transcript_visible_and_cancellation_restores_composer(self):
        for size in [(80, 24), (100, 32)]:
            with self.subTest(size=size), tempfile.TemporaryDirectory() as directory:
                app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None),
                    Settings('secret', 'https://api.invalid', 'deepseek-chat', Path(directory)))
                async with app.run_test(size=size) as pilot:
                    app._append_user('准备检查项目')
                    task = asyncio.create_task(app._request_approval({'name': 'run_command', 'args': {'command': 'python check.py'}}, 1, 1))
                    await pilot.pause()
                    self.assertIn('准备检查项目', transcript(app))
                    self.assertGreater(app.query_one('#transcript').size.height, 5)
                    self.assertLessEqual(app.query_one('#approval-panel').size.height, 11)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    await pilot.pause()
                    self.assertFalse(app.query_one('#approval-panel').display)
                    self.assertFalse(app.query_one('#composer', ChatInput).disabled)
                    self.assertEqual(app.focused.id, 'composer')

    async def test_delivery_once_then_plain_question_does_not_repeat_old_report(self):
        for status, label in [('verified', '已验证'), ('reviewed', '已静态审查'), ('unverified', '未验证'), ('failed', '验收失败')]:
            class Service:
                runtime_factory = session_store = None
                async def stream_turn(self, message, config, **kwargs):
                    if message == '任务':
                        report = {'status': status, 'pending': [{'id': 'a', 'description': 'secret 目标验收'}]}
                        yield TurnEvent('delivery', report)
                        yield TurnEvent('final', {'text': '任务回答', 'delivery': report})
                    else:
                        yield TurnEvent('final', {'text': '普通回答'})
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                app = TerminalAgentApp(Service(), Settings('secret', 'https://api.invalid', 'deepseek-chat', Path(directory)))
                async with app.run_test(size=(100, 32)) as pilot:
                    app._dispatch('任务')
                    await app.session_runner.wait_idle()
                    app._dispatch('你好')
                    await app.session_runner.wait_idle()
                    await pilot.pause()
                    text = transcript(app)
                    self.assertEqual(text.count('交付状态：'), 1)
                    self.assertIn(label, text)
                    self.assertLess(text.index('任务回答'), text.index('交付状态：'))
                    self.assertIn('普通回答', text)
                    self.assertNotIn('secret', text)

    async def test_task_pause_leaves_next_input_queued_until_explicit_resume(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        class Service:
            runtime_factory = session_store = None
            async def stream_turn(self, message, config, **kwargs):
                calls.append(message)
                if message == 'first':
                    entered.set()
                    await release.wait()
                    yield TurnEvent('task_paused', {'reason': '等待验收'})
                yield TurnEvent('final', {'text': message + ' answer'})
        with tempfile.TemporaryDirectory() as directory:
            app = TerminalAgentApp(Service(), Settings('secret', 'https://api.invalid', 'deepseek-chat', Path(directory)))
            async with app.run_test(size=(80, 24)) as pilot:
                original = app.thread_id
                app._dispatch('first')
                await asyncio.wait_for(entered.wait(), 2)
                app._dispatch('second')
                release.set()
                await app.session_runner.wait_idle()
                self.assertEqual(calls, ['first'])
                self.assertEqual(len(app.session_runner.queue), 1)
                self.assertEqual(app.thread_id, original)
                self.assertIn('等待验收', transcript(app))
                app._dispatch('/queue resume')
                await self.wait_for_condition(pilot,
                    lambda: calls==['first','second'] and app.session_runner.state=='idle',
                    'resumed queued input to finish')
                self.assertEqual(calls, ['first', 'second'])

    async def test_ready_green_and_no_color_fallback(self):
        for no_color in [False, True]:
            environment = dict(os.environ)
            environment.pop('NO_COLOR', None)
            if no_color:
                environment['NO_COLOR'] = '1'
            with patch.dict(os.environ, environment, clear=True), tempfile.TemporaryDirectory() as directory:
                app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None),
                    Settings('secret', 'https://api.invalid', 'deepseek-chat', Path(directory)))
                async with app.run_test(size=(80, 24)):
                    row = app.query_one('#status', Static).content
                    style = row.get_style_at_offset(app.console, 0)
                    self.assertEqual(style.color.get_truecolor().hex if style.color else None, None if no_color else '#3fb950')
