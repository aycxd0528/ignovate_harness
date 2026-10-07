"""Approved spinner animates in place and stops at terminal states."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from textual.widgets import RichLog
from agent_service import TurnEvent
from config import Settings
from tui import TerminalAgentApp

class ActivitySpinnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_thinking_and_tools_rotate_in_place_with_elapsed_time(self):
        with tempfile.TemporaryDirectory() as root:
            app=TerminalAgentApp(SimpleNamespace(runtime_factory=None,session_store=None),
                                 Settings('key','https://api.invalid','model',Path(root)))
            async with app.run_test(size=(80,24)) as pilot:
                log=app.query_one('#transcript',RichLog)
                app._append_user('检查项目')
                reply=app._ensure_live_response()
                clock=100.0
                timer=Mock()
                with patch('tui.monotonic',side_effect=lambda:clock), patch.object(app,'set_interval',return_value=timer):
                    app._set_status('thinking')
                    app._stop_status_timer()
                    await pilot.pause()
                    first='\n'.join(line.text for line in log.lines)
                    self.assertTrue(any(char in first for char in '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'))
                    retained=len(log._entries)
                    clock=101.2
                    app._animate_status()
                    await pilot.pause()
                    second='\n'.join(line.text for line in log.lines)
                    self.assertNotEqual(first,second)
                    self.assertIn('1.2s',second)
                    self.assertEqual(len(log._entries),retained)
                    for name in ['read_file','run_command']:
                        event=TurnEvent('tool_start',{'call_id':name,'name':name,
                            'preview':{'path':'README.md','command':'python -m unittest'}})
                        app._append_activity(event,[])
                        app._stop_status_timer()
                        await pilot.pause()
                        clock+=1
                        app._animate_status()
                        await pilot.pause()
                        before='\n'.join(line.text for line in log.lines)
                        clock+=1
                        app._animate_status()
                        await pilot.pause()
                        after='\n'.join(line.text for line in log.lines)
                        self.assertNotEqual(before,after)
                        self.assertIn('2.0s',after)
                        self.assertIn('python -m unittest' if name=='run_command' else 'README.md',after)
                        self.assertEqual(len(log._entries),retained)
                        app._append_activity(TurnEvent('tool_end',{'call_id':name,'name':name,'ok':True,'elapsed_ms':2000}),[])
                        app._stop_status_timer()
                    app._append_assistant('最终回复')
                    app._set_status('ready')
                    await pilot.pause()
                    text='\n'.join(line.text for line in log.lines)
                    self.assertNotIn('执行中',text)
                    self.assertNotIn('正在推理',text)
                    self.assertIsNone(app._status_timer)
                    self.assertIsNone(reply.pending_activity)

    async def test_approval_pause_and_failure_stop_the_timer(self):
        with tempfile.TemporaryDirectory() as root:
            app=TerminalAgentApp(SimpleNamespace(runtime_factory=None,session_store=None),
                                 Settings('key','https://api.invalid','model',Path(root)))
            async with app.run_test(size=(80,24)) as pilot:
                for state in ['waiting_approval','paused','error','ready']:
                    timer=Mock()
                    app._status_timer=timer
                    app._set_status(state)
                    timer.stop.assert_called_once()
                    self.assertIsNone(app._status_timer)

    async def test_reduced_motion_uses_static_marker_and_slow_real_elapsed_updates(self):
        import json
        with tempfile.TemporaryDirectory() as root:
            config = Path(root)/'.nailong/settings.json'
            config.parent.mkdir()
            config.write_text(json.dumps({'ui': {'reduced_motion': True}}))
            app=TerminalAgentApp(SimpleNamespace(runtime_factory=None,session_store=None),
                                 Settings('key','https://api.invalid','model',Path(root)))
            async with app.run_test(size=(80,24)) as pilot:
                app._append_user('检查项目')
                reply = app._ensure_live_response()
                with patch('tui.monotonic', return_value=10):
                    app._set_status('thinking')
                self.assertTrue(app._reduced_motion)
                self.assertEqual(app._status_timer._interval, 1.0)
                first = reply.spinner
                with patch('tui.monotonic', return_value=12):
                    app._animate_status()
                self.assertEqual(first, reply.spinner)
                self.assertEqual(reply.elapsed_seconds, 2)
                app._set_status('ready')
                self.assertIsNone(app._status_timer)

if __name__ == '__main__':
    unittest.main()
