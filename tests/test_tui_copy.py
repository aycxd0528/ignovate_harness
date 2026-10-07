"""Copy raw replies and selections without stopping the running conversation."""
import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from config import Settings
from agent_service import TurnEvent
from tui import TerminalAgentApp
from textual.widgets import Button, TextArea


class CopyReplyTests(unittest.IsolatedAsyncioTestCase):
    def app(self, root, service=None):
        return TerminalAgentApp(service or SimpleNamespace(runtime_factory=None, session_store=None),
            Settings('sentinel-secret', 'https://api.invalid', 'deepseek-chat', Path(root)))

    async def test_quick_copy_preserves_markdown_and_stream_updates(self):
        with tempfile.TemporaryDirectory() as root, patch('shutil.which', return_value=None):
            app = self.app(root)
            async with app.run_test(size=(100, 32)) as pilot:
                previous = '上一条 **回复**\n\n```python\nprint("中文")\n```\n'
                app._append_user('first')
                app._append_assistant(previous)
                app._append_user('second')
                app._update_live_response('正在生成的正文')
                await pilot.press('f6')
                await pilot.pause()
                self.assertEqual(app.clipboard, '正在生成的正文')
                await pilot.press('shift+f6')
                await pilot.pause()
                self.assertEqual(app.clipboard, previous)
                app._update_live_response('正在生成的正文\n第二行')
                await pilot.press('f6')
                await pilot.pause()
                self.assertEqual(app.clipboard, '正在生成的正文\n第二行')
                final = '最终回复\n\n| 列 | 值 |\n| --- | --- |\n| 一 | 二 |'
                app._append_assistant(final)
                await pilot.press('f6')
                await pilot.pause()
                self.assertEqual(app.clipboard, final)
                await pilot.press('shift+f6')
                await pilot.pause()
                self.assertEqual(app.clipboard, previous, '流式更新不应产生重复回复')

    async def test_selected_copy_does_not_stop_task_or_change_composer(self):
        with tempfile.TemporaryDirectory() as root, patch('shutil.which', return_value=None):
            app = self.app(root)
            async with app.run_test(size=(80, 24)) as pilot:
                app._append_assistant('中文正文\n\n```text\nline 1\nline 2\n```')
                app.query_one('#composer', TextArea).text = '保留输入'
                await pilot.press('f7')
                await pilot.pause()
                body = app.screen.query_one('#copy-body', TextArea)
                self.assertTrue(body.read_only)
                original = body.text
                app.session_runner.stop = AsyncMock()
                await pilot.press('home', 'shift+right', 'shift+right', 'ctrl+c')
                await pilot.pause()
                self.assertEqual(app.clipboard, '中文')
                app.session_runner.stop.assert_not_awaited()
                await pilot.press('x', 'backspace', 'ctrl+x')
                self.assertEqual(body.text, original)
                await pilot.press('ctrl+a', 'ctrl+c')
                await pilot.pause()
                self.assertEqual(app.clipboard, original)
                await pilot.press('escape')
                await pilot.pause()
                self.assertEqual(app.query_one('#composer', TextArea).text, '保留输入')
                self.assertEqual(app.focused.id, 'composer')

    async def test_copy_screen_freezes_live_snapshot_and_can_refresh_or_browse(self):
        with tempfile.TemporaryDirectory() as root, patch('shutil.which', return_value=None):
            app = self.app(root)
            async with app.run_test(size=(100, 32)) as pilot:
                app._append_assistant('之前的回复')
                app._update_live_response('打开时的正文')
                await pilot.press('f7')
                await pilot.pause()
                body = app.screen.query_one('#copy-body', TextArea)
                self.assertEqual(body.text, '打开时的正文')
                app._update_live_response('更新后的正文')
                await pilot.pause()
                self.assertEqual(body.text, '打开时的正文', '流式更新不应破坏复制选区')
                await pilot.press('f5')
                await pilot.pause()
                self.assertEqual(body.text, '更新后的正文')
                await pilot.click('#copy-previous')
                await pilot.pause()
                self.assertEqual(body.text, '之前的回复')
                app.copy_to_clipboard('原剪贴板')
                await pilot.press('shift+f6')
                await pilot.pause()
                self.assertEqual(app.clipboard, '原剪贴板', '没有更早的回复时不能复制当前条冒充上一条')
                await pilot.press('f6')
                await pilot.pause()
                self.assertEqual(app.clipboard, '之前的回复')
                await pilot.click('#copy-next')
                await pilot.pause()
                self.assertEqual(body.text, '更新后的正文')
                await pilot.press('escape')

    async def test_mouse_drag_can_copy_unicode_selected_text(self):
        with tempfile.TemporaryDirectory() as root, patch('shutil.which', return_value=None):
            app = self.app(root)
            async with app.run_test(size=(80, 24)) as pilot:
                app._append_assistant('中文abc\nsecond line')
                await pilot.press('f7')
                await pilot.pause()
                body = app.screen.query_one('#copy-body', TextArea)
                origin = (body.gutter.left + body.gutter_width, body.gutter.top)
                await pilot.mouse_down('#copy-body', offset=origin)
                await pilot.hover('#copy-body', offset=(origin[0] + 4, origin[1]))
                await pilot.mouse_up('#copy-body', offset=(origin[0] + 4, origin[1]))
                self.assertEqual(body.selected_text, '中文')
                await pilot.press('ctrl+c')
                await pilot.pause()
                self.assertEqual(app.clipboard, '中文')
                await pilot.press('escape')

    async def test_copy_while_running_keeps_stream_and_tool_fold_state(self):
        release, started = asyncio.Event(), asyncio.Event()
        class Service:
            runtime_factory = None
            session_store = None
            async def stream_turn(self, _message, _config, **_kwargs):
                yield TurnEvent('token', {'text': '正在生成的回复'})
                started.set()
                await release.wait()
                yield TurnEvent('final', {'text': '完整回复'})
        with tempfile.TemporaryDirectory() as root, patch('shutil.which', return_value=None):
            app = self.app(root, Service())
            async with app.run_test(size=(100, 32)) as pilot:
                app._dispatch('启动生成')
                await asyncio.wait_for(started.wait(), 3)
                try:
                    await pilot.press('f7')
                    await pilot.pause()
                    self.assertTrue(app.session_runner.busy)
                    await pilot.press('ctrl+a', 'ctrl+c')
                    await pilot.pause()
                    self.assertEqual(app.clipboard, '正在生成的回复')
                    self.assertTrue(app.session_runner.busy)
                    self.assertFalse(app._tools_expanded)
                    release.set()
                    await app.session_runner.wait_idle()
                    await pilot.pause()
                    self.assertEqual(app.screen.query_one('#copy-body', TextArea).text, '正在生成的回复')
                    await pilot.press('f5')
                    await pilot.pause()
                    self.assertEqual(app.screen.query_one('#copy-body', TextArea).text, '完整回复')
                    self.assertEqual(app.completed_turns, 1)
                    await pilot.press('escape')
                finally:
                    release.set()
                    await app.session_runner.wait_idle()

    async def test_closing_copy_screen_during_async_copy_does_not_crash(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow_copy(text):
            entered.set()
            await release.wait()
            return False
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            async with app.run_test(size=(80, 24)) as pilot:
                app._append_assistant('可复制正文')
                await pilot.press('f7')
                await pilot.pause()
                with patch.object(app, '_copy_text', slow_copy):
                    await pilot.press('f6')
                    await asyncio.wait_for(entered.wait(), 3)
                    await pilot.press('escape')
                    await pilot.pause()
                    release.set()
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    self.assertEqual(app.focused.id, 'composer')

    async def test_history_and_clear_cannot_copy_another_sessions_reply(self):
        service = SimpleNamespace(runtime_factory=None, session_store=None,
            get_history=lambda _: [('user', '恢复会话'), ('assistant', '历史 sentinel-secret 回复'),
                                  ('tool', '{"output":"private"}'),
                                  ('assistant', '[{"type":"tool_call","name":"read_file"}]'),
                                  ('assistant', '历史最终回复')])
        with tempfile.TemporaryDirectory() as root, patch('shutil.which', return_value=None):
            app = self.app(root, service)
            async with app.run_test(size=(80, 24)) as pilot:
                app._append_assistant('另一个会话的回复')
                app._show_history(replace=True)
                await pilot.press('f6')
                await pilot.pause()
                self.assertEqual(app.clipboard, '历史最终回复')
                await pilot.press('shift+f6')
                await pilot.pause()
                self.assertNotIn('sentinel-secret', app.clipboard)
                self.assertIn('[密钥已隐藏]', app.clipboard)
                app._dispatch('/clear')
                await pilot.pause()
                app.clipboard_before = app.clipboard
                await pilot.press('f6', 'f7')
                await pilot.pause()
                self.assertEqual(app.clipboard, app.clipboard_before)
                self.assertFalse(app.query('#copy-body'))

    async def test_copy_controls_fit_both_terminal_sizes_and_keep_weather(self):
        with tempfile.TemporaryDirectory() as root:
            for size in [(100, 32), (80, 24)]:
                app = self.app(root)
                async with app.run_test(size=size) as pilot:
                    app._append_assistant('正文\n' * 150)
                    await pilot.pause()
                    self.assertFalse(app.query('#selection-hint'))
                    self.assertLessEqual(app.query_one('#token-weather').region.bottom, size[1])
                    await pilot.press('f7')
                    await pilot.pause()
                    body = app.screen.query_one('#copy-body', TextArea)
                    self.assertGreater(body.size.height, 5)
                    for selector in ['#copy-previous', '#copy-next', '#copy-refresh',
                                     '#copy-close']:
                        control = app.screen.query_one(selector, Button)
                        self.assertGreater(control.region.x, 0)
                        self.assertLessEqual(control.region.right, size[0])
                        self.assertLessEqual(control.region.bottom, size[1])
                    await pilot.press('escape')

    async def test_clipboard_emits_osc52_and_uses_mac_native_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            async with app.run_test(size=(80, 24)):
                text = '中文\n```python\nx = 1\n```'
                process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'', b'')),
                                          kill=Mock(), wait=AsyncMock())
                with patch('tui.sys.platform', 'darwin'), patch('shutil.which', return_value='/usr/bin/pbcopy'), \
                        patch('asyncio.create_subprocess_exec', AsyncMock(return_value=process)) as spawn, \
                        patch.object(app._driver, 'write') as write:
                    native = await app._copy_text(text)
                self.assertTrue(native)
                self.assertEqual(app.clipboard, text)
                expected = '\x1b]52;c;' + base64.b64encode(text.encode()).decode() + '\a'
                self.assertIn(expected, [call.args[0] for call in write.call_args_list])
                self.assertEqual(spawn.call_args.args, ('/usr/bin/pbcopy',))
                self.assertEqual(spawn.call_args.kwargs['stdin'], asyncio.subprocess.PIPE)
                process.communicate.assert_awaited_once_with(text.encode('utf-8'))

    async def test_failed_native_copy_keeps_textual_clipboard_and_honest_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            async with app.run_test(size=(80, 24)):
                with patch('tui.sys.platform', 'darwin'), patch('shutil.which', return_value='/usr/bin/pbcopy'), \
                        patch('asyncio.create_subprocess_exec', AsyncMock(side_effect=OSError('unavailable'))):
                    native = await app._copy_text('仍可选择的正文')
                self.assertFalse(native)
                self.assertEqual(app.clipboard, '仍可选择的正文')

    async def test_native_copy_timeout_reaps_process_without_losing_clipboard(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            async with app.run_test(size=(80, 24)):
                process = SimpleNamespace(returncode=None, communicate=AsyncMock(side_effect=asyncio.TimeoutError),
                                          kill=Mock(), wait=AsyncMock())
                with patch('tui.sys.platform', 'darwin'), patch('shutil.which', return_value='/usr/bin/pbcopy'), \
                        patch('asyncio.create_subprocess_exec', AsyncMock(return_value=process)):
                    native = await app._copy_text('超时仍保留正文')
                self.assertFalse(native)
                self.assertEqual(app.clipboard, '超时仍保留正文')
                process.kill.assert_called_once()
                process.wait.assert_awaited_once()
