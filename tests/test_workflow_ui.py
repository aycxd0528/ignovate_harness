from platform_fixtures import python_command
import asyncio
import tempfile
import unittest
from pathlib import Path
from config import Settings
from tui import TerminalAgentApp,ChatInput
from agent_service import TurnEvent
from nailong.core.sessions import ProjectSessionStore
from types import SimpleNamespace

class WorkflowUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_textual_queue_binding_and_overflow_keep_input(self):
        with tempfile.TemporaryDirectory() as directory:
            service=SimpleNamespace(runtime_factory=None,session_store=None)
            app=TerminalAgentApp(service,Settings('key','https://api.invalid','deepseek-flash',Path(directory)))
            async with app.run_test(size=(80,24)) as pilot:
                original=app.thread_id
                app.session_runner.state='paused'
                app._dispatch('first'); app._dispatch('/clear')
                self.assertEqual(app.thread_id,original)
                for index in range(9): app._dispatch(str(index))
                composer=app.query_one('#composer',ChatInput); composer.text='overflow'
                app.on_input_submitted(ChatInput.Submitted('overflow'))
                self.assertEqual(composer.text,'overflow')

    async def test_textual_queued_verify_approval_without_worker_context(self):
        import json
        from agent import AgentRuntimeFactory
        from agent_service import AgentService
        with tempfile.TemporaryDirectory() as directory,tempfile.TemporaryDirectory() as data:
            root=Path(directory); (root/'.nailong').mkdir()
            (root/'.nailong/settings.json').write_text(json.dumps({'verification':{'steps':[{'name':'test','kind':'test','command':python_command('pass')}]}}), newline='\n')
            settings=Settings('key','https://api.invalid','deepseek-flash',root)
            factory=AgentRuntimeFactory(settings,session_store=ProjectSessionStore(root,base_dir=data))
            app=TerminalAgentApp(AgentService(factory,session_store=factory.session_store),settings)
            async with app.run_test(size=(80,24)) as pilot:
                app._dispatch('/verify'); await pilot.pause()
                self.assertTrue(app.query_one('#approval-panel').display)
                self.assertEqual(len(app.screen_stack),1)
                await pilot.press('escape'); await pilot.pause(); await app.session_runner.wait_idle()
                self.assertEqual(factory.session_store.read_events(app.thread_id)[-1]['data']['status'],'unverified')
    async def test_textual_queues_input_and_ctrl_c_preserves_identity(self):
        with tempfile.TemporaryDirectory() as directory,tempfile.TemporaryDirectory() as data:
            started=asyncio.Event(); gate=asyncio.Event(); calls=[]
            class Service:
                session_store=ProjectSessionStore(directory,base_dir=data)
                runtime_factory=None
                async def stream_turn(self,message,config,**kwargs):
                    calls.append(message); started.set(); await gate.wait(); yield TurnEvent('final',{'text':'done'})
            app=TerminalAgentApp(Service(),Settings('key','https://api.invalid','deepseek-flash',Path(directory)))
            async with app.run_test(size=(100,32)) as pilot:
                original=app.thread_id
                app._dispatch('first'); await asyncio.wait_for(started.wait(),2)
                self.assertFalse(app.query_one('#composer',ChatInput).disabled)
                app._dispatch('second'); await pilot.pause()
                self.assertEqual(calls,['first']); self.assertEqual(len(app.session_runner.queue),1)
                await pilot.press('ctrl+c'); await pilot.pause()
                self.assertEqual(app.thread_id,original); self.assertEqual(app.session_runner.state,'paused')
                self.assertEqual(len(app.session_runner.queue),1)
                gate.set(); app._dispatch('/queue resume'); await pilot.pause(); await app.session_runner.wait_idle()
                self.assertEqual(calls,['first','second'])
    async def test_textual_new_local_commands_and_light_theme(self):
        with tempfile.TemporaryDirectory() as directory:
            service=SimpleNamespace(runtime_factory=None,session_store=None)
            app=TerminalAgentApp(service,Settings('key','https://api.invalid','deepseek-flash',Path(directory)))
            async with app.run_test(size=(80,24)) as pilot:
                app._dispatch('/theme light'); await pilot.pause()
                self.assertEqual(app._ui_theme.name,'light')
                self.assertEqual(app.screen.styles.background.hex,'#F5F6F7')
