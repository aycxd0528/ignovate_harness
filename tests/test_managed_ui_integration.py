"""Current task, queued input and approval boundaries in the real Textual app."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from io import StringIO
from rich.console import Console as RichConsole

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from textual.widgets import RichLog, Static

from agent import AgentRuntimeFactory
from agent_service import AgentService, TurnEvent
from config import Settings
from nailong.core.permissions import PermissionEngine
from nailong.core.sessions import ProjectSessionStore
from tui import TerminalAgentApp
import local_tools


class ManagedUiIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_delayed_status_tick_does_not_access_removed_widget(self):
        with tempfile.TemporaryDirectory() as directory:
            app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None),
                Settings('fake', 'https://api.invalid', 'deepseek-flash', Path(directory)))
            async with app.run_test(size=(80, 24)) as pilot:
                app._set_status('thinking')
                await app.query_one('#status', Static).remove()
                app._animate_status()
                await pilot.pause()
                self.assertIsNone(app._status_timer)

    async def test_inline_project_switch_does_not_change_other_default_roots(self):
        from tests.test_ui import InlinePromptSession
        from ui.app import run_inline
        from ui.console import Console
        from ui.theme import Theme
        original_root = local_tools.PROJECT_ROOT
        self.addCleanup(setattr, local_tools, 'PROJECT_ROOT', original_root)
        factories = []
        def build(settings):
            factory = AgentRuntimeFactory(settings)
            factories.append(factory)
            return factory
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first, second = base / 'first', base / 'second'
            first.mkdir()
            second.mkdir()
            (second / 'identity.txt').write_text('inline second project', newline='\n')
            output = StringIO()
            with patch.dict(os.environ, {'NAILONG_DATA_DIR': str(base / 'private')}), \
                    patch('ui.app.AgentRuntimeFactory', side_effect=build):
                await run_inline(Settings('fake', 'https://api.invalid', 'deepseek-flash', first), plain=True,
                    prompt_session=InlinePromptSession([f'/project {second}', '/exit']),
                    console=Console(theme=Theme(no_color=True), console=RichConsole(file=output, no_color=True)))
            self.assertNotIn('切换项目失败', output.getvalue())
            self.assertEqual(factories[-1]._tool_session('reader').read_file('identity.txt')['content'], 'inline second project')
            self.assertEqual(local_tools.PROJECT_ROOT, original_root)

    async def test_project_switch_binds_real_tools_without_changing_other_default_roots(self):
        class Model(FakeMessagesListChatModel):
            def bind_tools(self, tools, **options):
                return self
        original_root = local_tools.PROJECT_ROOT
        self.addCleanup(setattr, local_tools, 'PROJECT_ROOT', original_root)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first, second = base / 'first', base / 'second'
            first.mkdir()
            second.mkdir()
            (second / 'identity.txt').write_text('second project', newline='\n')
            settings = Settings('fake', 'https://api.invalid', 'deepseek-flash', first)
            with patch('agent.ChatDeepSeek', return_value=Model(responses=[AIMessage(content='done')])):
                factory = AgentRuntimeFactory(settings, session_store=ProjectSessionStore(first, base_dir=base / 'private'))
                app = TerminalAgentApp(AgentService(factory), settings, thread_id='before')
                async with app.run_test(size=(80, 24)) as pilot:
                    app._dispatch(f'/project {second}')
                    await pilot.pause()
                    selected = app.service.runtime_factory._tool_session(app.thread_id)
                    self.assertEqual(selected.read_file('identity.txt')['content'], 'second project')
                    self.assertNotEqual(app.thread_id, 'before')
                    self.assertEqual(local_tools.PROJECT_ROOT, original_root)

    async def test_paused_task_retains_queue_until_explicit_resume(self):
        started, finish = asyncio.Event(), asyncio.Event()
        class Service:
            runtime_factory = session_store = None
            async def stream_turn(self, message, config, **options):
                if message == '调查问题':
                    started.set()
                    await finish.wait()
                    yield TurnEvent('task_paused', {'reason': '连续读取没有新进展'})
                    yield TurnEvent('final', {'text': '调查已暂停', 'blocked': True})
                else:
                    yield TurnEvent('final', {'text': '队列输入已执行'})
        with tempfile.TemporaryDirectory() as directory:
            app = TerminalAgentApp(Service(), Settings('fake', 'https://api.invalid', 'deepseek-flash', Path(directory)))
            async with app.run_test(size=(80, 24)) as pilot:
                app._dispatch('调查问题')
                await asyncio.wait_for(started.wait(), 3)
                app._dispatch('继续调查')
                finish.set()
                await app.session_runner.wait_idle()
                await pilot.pause()
                self.assertEqual(app.session_runner.state, 'paused')
                self.assertEqual(len(app.session_runner.queue), 1)
                self.assertEqual(app._status, 'paused')
                text = '\n'.join(line.text for line in app.query_one('#transcript', RichLog).lines)
                self.assertIn('调查已暂停', text)
                self.assertNotIn('队列输入已执行', text)
                app._dispatch('/queue resume')
                async def resumed_reply():
                    while True:
                        await pilot.pause()
                        text='\n'.join(line.text for line in app.query_one('#transcript',RichLog).lines)
                        if app.session_runner.state=='idle' and '队列输入已执行' in text:
                            return
                await asyncio.wait_for(resumed_reply(),3)
                self.assertEqual(app.session_runner.state, 'idle')
                self.assertEqual(app.session_runner.queue, [])
                text = '\n'.join(line.text for line in app.query_one('#transcript', RichLog).lines)
                self.assertIn('队列输入已执行', text)

    async def test_local_task_acceptance_and_completed_chat_do_not_reuse_delivery(self):
        class Model(FakeMessagesListChatModel):
            def bind_tools(self, tools, **options):
                return self
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'project'
            root.mkdir()
            (root / 'source.py').write_text('value = 1\n', newline='\n')
            settings = Settings('fake', 'https://api.invalid', 'deepseek-flash', root)
            store = ProjectSessionStore(root, base_dir=base / 'private')
            with patch('agent.ChatDeepSeek', return_value=Model(responses=[AIMessage(content='你好，这是普通回复。')])):
                factory = AgentRuntimeFactory(settings, session_store=store)
            service = AgentService(factory, permission_engine=PermissionEngine(root))
            app = TerminalAgentApp(service, settings, thread_id='owner')
            async with app.run_test(size=(100, 32)) as pilot:
                for command in ('/task new 核对界面', '/task scope source.py',
                        '/task constraint 保留用户内容', '/task accept layout manual 手工核对输入布局', '/task status'):
                    await app._perform_request(app.controller.resolve(command))
                task = factory.task_store.snapshot('owner')
                self.assertEqual(task['constraints'], ['保留用户内容'])
                self.assertEqual(task['acceptance'][0]['status'], 'pending')
                self.assertEqual((await service.delivery_report('owner'))['status'], 'unverified')
                for command in ('/task confirm layout 已在终端核对', '/task complete'):
                    await app._perform_request(app.controller.resolve(command))
                self.assertEqual(factory.task_store.snapshot('owner')['lifecycle'], 'completed')
                self.assertEqual((await service.delivery_report('owner'))['status'], 'verified')
                app._clear_transcript()
                await app._perform_request(app.controller.resolve('你好'))
                await pilot.pause()
                text = '\n'.join(line.text for line in app.query_one('#transcript', RichLog).lines)
                self.assertEqual(text.count('你好，这是普通回复。'), 1)
                self.assertNotIn('手工核对输入布局', text)
                self.assertNotIn('verified', text)
                finals = [row['data'] for row in store.read_events('owner') if row['kind'] == 'final']
                self.assertIsNone(finals[-1].get('delivery'))

    async def test_expanded_approval_keeps_choices_composer_and_metrics_on_screen(self):
        for width, height in ((100, 32), (80, 24)):
            with self.subTest(size=(width, height)), tempfile.TemporaryDirectory() as directory:
                settings = Settings('secret-sentinel', 'https://api.invalid', 'deepseek-flash', Path(directory))
                app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None), settings)
                async with app.run_test(size=(width, height)) as pilot:
                    app._append_user('修改文件前展示审批')
                    app._append_assistant('已准备好修改，请核对完整内容。')
                    pending = asyncio.create_task(app._request_approval({'name': 'write_file',
                        'args': {'path': 'source.py', 'content': 'content\n' * 100},
                        '_approval': {'suggested_rule': 'Write(./source.py)'}}, 1, 1))
                    try:
                        await pilot.pause()
                        await pilot.press('d')
                        await pilot.pause()
                        panel = app.query_one('#approval-panel')
                        self.assertLessEqual(panel.query_one('#approval-hint').region.bottom, panel.region.bottom)
                        self.assertLessEqual(panel.query_one('#approval-choices').region.bottom, panel.region.bottom)
                        self.assertLessEqual(panel.region.bottom, app.query_one('#composer-frame').region.y)
                        self.assertLessEqual(app.query_one('#composer-stats').region.bottom, height)
                        self.assertGreater(app.query_one('#transcript').size.height, 0)
                        self.assertIn('deepseek-flash', app.query_one('#composer-stats', Static).content.plain)
                        await pilot.press('escape')
                        self.assertEqual(await asyncio.wait_for(pending, 3), 'reject')
                    finally:
                        if not pending.done():
                            pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
