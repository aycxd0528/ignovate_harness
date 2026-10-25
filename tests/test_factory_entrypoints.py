"""Resource ownership through real entrypoints and temporary native stores."""
import asyncio
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack

from rich.console import Console as RichConsole
from agent import AgentRuntimeFactory
from config import Settings
from nailong.core.sessions import ProjectSessionStore
from ui.app import run_inline
from ui.console import Console
from ui.theme import Theme


class FactoryEntrypointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-chat', self.root)
        self.factory = None
        self.factories = []
        self.output = io.StringIO()
        self.console = Console(theme=Theme(no_color=True), console=RichConsole(file=self.output, no_color=True))

        def factory(settings):
            store = ProjectSessionStore(settings.project_root, base_dir=self.root / 'state')
            result = AgentRuntimeFactory(settings, session_store=store)
            self.factory = result
            self.factories.append(result)
            self.addAsyncCleanup(result.aclose)
            return result

        for replacement in (patch('agent.ChatDeepSeek', return_value=object()),
                            patch('ui.app.AgentRuntimeFactory', side_effect=factory)):
            replacement.start()
            self.addCleanup(replacement.stop)

    def assert_closed(self, factory):
        with self.assertRaises(sqlite3.ProgrammingError):
            factory.checkpointer.conn.execute('SELECT 1')

    async def test_session_list_failure_closes_native_factory(self):
        with patch.object(ProjectSessionStore, 'list_sessions', side_effect=OSError('session list failed')):
            with self.assertRaisesRegex(OSError, 'session list failed'):
                await run_inline(self.settings, console=self.console)
        self.assert_closed(self.factory)

    async def test_cancel_startup_resume_prompt_closes_native_factory(self):
        entered = asyncio.Event()

        class Prompt:
            async def prompt_async(self, *args, **kwargs):
                entered.set()
                await asyncio.Event().wait()

        with patch.object(ProjectSessionStore, 'list_sessions', return_value=[{'thread_id': 'old'}]):
            operation = asyncio.create_task(run_inline(self.settings, prompt_session=Prompt(), console=self.console))
            await asyncio.wait_for(entered.wait(), 2)
            operation.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await operation
        self.assert_closed(self.factory)

    async def test_failed_candidate_is_closed_before_next_prompt(self):
        other = self.root / 'other'
        other.mkdir()
        answers = iter([f'/project {other}', '/exit'])
        owner = self

        class Prompt:
            async def prompt_async(self, *_args, **_kwargs):
                if len(owner.factories) == 2:
                    owner.assert_closed(owner.factories[-1])
                    owner.assertEqual(owner.factories[0].checkpointer.conn.execute('SELECT 1').fetchone()[0], 1)
                return next(answers)

        from agent_service import AgentService

        def service(factory, **kwargs):
            if factory.settings.project_root == other:
                raise OSError('candidate setup failed')
            return AgentService(factory, **kwargs)

        with patch('ui.app.AgentService', side_effect=service):
            await run_inline(self.settings, prompt_session=Prompt(), console=self.console)
        self.assertEqual(len(self.factories), 2)
        self.assertIn('candidate setup failed', self.output.getvalue())

    def headless_runtime(self):
        from test_context_runtime import RecordingModel
        from langchain_core.messages import AIMessage
        with patch('agent.ChatDeepSeek', return_value=RecordingModel(responses=[AIMessage(content='offline answer')])):
            factory = AgentRuntimeFactory(self.settings, session_store=ProjectSessionStore(
                self.root, base_dir=self.root / 'headless-state'))
        self.addAsyncCleanup(factory.aclose)
        return factory, factory(thread_id='headless-fixture')

    async def test_headless_close_failure_is_reported_as_error_and_redacted(self):
        import headless
        factory, runtime = self.headless_runtime()
        native_close = factory.aclose

        async def close():
            await native_close()
            raise OSError('close failed fixture-key')

        with patch.object(headless, 'create_agent_runtime', return_value=runtime), \
                patch.object(factory, 'aclose', new=close):
            code = await headless.run_print(self.settings, 'offline task', output_format='json', stdout=self.output)
        payload = json.loads(self.output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(payload['status'], 'error')
        self.assertIn('close failed', payload['error'])
        self.assertNotIn('fixture-key', self.output.getvalue())
        self.assert_closed(factory)

    async def test_headless_driver_close_failure_still_closes_native_factory(self):
        import headless
        factory, runtime = self.headless_runtime()
        stack = ExitStack()

        def fail():
            raise OSError('driver close failed')

        stack.callback(fail)
        with patch.object(headless, 'create_agent_runtime', return_value=runtime), \
                patch.object(headless, 'ExitStack', return_value=stack):
            code = await headless.run_print(self.settings, 'offline task', output_format='json', stdout=self.output)
        self.assertEqual(code, 1)
        self.assertIn('driver close failed', json.loads(self.output.getvalue())['error'])
        self.assert_closed(factory)

    async def test_headless_delivery_refresh_failure_still_closes_native_factory(self):
        import headless
        factory, runtime = self.headless_runtime()
        with patch.object(headless, 'create_agent_runtime', return_value=runtime), \
                patch('agent_service.AgentService.delivery_report', side_effect=RuntimeError('delivery refresh failed')):
            code = await headless.run_print(self.settings, '/status', output_format='json', stdout=self.output)
        self.assertEqual(code, 1)
        self.assertIn('delivery refresh failed', json.loads(self.output.getvalue())['error'])
        self.assert_closed(factory)

    async def test_gui_service_initialization_failure_rolls_back_before_publish(self):
        from nailong.gui.controller import GUIController
        controller = GUIController(self.root, sessions=ProjectSessionStore(self.root, base_dir=self.root / 'gui-state'))
        self.addAsyncCleanup(controller.close)
        factories = []

        def factory(*args, **kwargs):
            result = AgentRuntimeFactory(*args, **kwargs)
            factories.append(result)
            self.addAsyncCleanup(result.aclose)
            return result

        with patch('config.load_settings', return_value=self.settings), \
                patch('agent.AgentRuntimeFactory', side_effect=factory), \
                patch('agent_service.AgentService', side_effect=OSError('GUI service setup failed')):
            with self.assertRaisesRegex(OSError, 'GUI service setup failed'):
                controller.ensure_service()
        self.assert_closed(factories[0])
        self.assertIsNone(controller.factory)
        self.assertIsNone(controller.service)

    async def test_tui_session_list_failure_closes_native_factory(self):
        import tui
        factory = self.factory_fixture()
        with patch.object(tui, 'AgentRuntimeFactory', return_value=factory), \
                patch.object(ProjectSessionStore, 'list_sessions', side_effect=OSError('TUI list failed')):
            with self.assertRaisesRegex(OSError, 'TUI list failed'):
                tui.run_tui(self.settings)
        self.assert_closed(factory)

    def factory_fixture(self):
        factory = AgentRuntimeFactory(self.settings, session_store=ProjectSessionStore(
            self.root, base_dir=self.root / ('runtime-' + str(len(self.factories)))))
        self.factories.append(factory)
        self.addAsyncCleanup(factory.aclose)
        return factory

    async def test_tui_framework_failure_closes_before_owner_loop_exits(self):
        from tui import TerminalAgentApp
        from agent_service import AgentService
        factory = self.factory_fixture()
        await factory.async_runtime(thread_id='tui-startup')
        saver = factory.async_checkpointer
        app = TerminalAgentApp(AgentService(factory), self.settings)
        self.addAsyncCleanup(app.session_runner.close)
        with patch('textual.app.App.run_async', side_effect=OSError('framework startup failed')):
            with self.assertRaisesRegex(OSError, 'framework startup failed'):
                await app.run_async(headless=True)
        self.assert_closed(factory)
        with self.assertRaises(ValueError):
            await saver.conn.execute('SELECT 1')

    async def test_tui_runner_close_failure_still_releases_all_factories(self):
        from tui import TerminalAgentApp
        from agent_service import AgentService
        first, second = self.factory_fixture(), self.factory_fixture()
        app = TerminalAgentApp(AgentService(first), self.settings)
        app.runtime_factories.append(second)
        native_close = app.session_runner.close

        async def close():
            await native_close()
            raise OSError('runner late close failed')

        with patch.object(app.session_runner, 'close', new=close):
            with self.assertRaisesRegex(OSError, 'runner late close failed'):
                await app.on_unmount()
        self.assert_closed(first)
        self.assert_closed(second)

    async def test_tui_candidate_validation_does_not_close_selected_factory(self):
        from tui import TerminalAgentApp
        from agent_service import AgentService
        factory = self.factory_fixture()
        candidate = self.factory_fixture()
        app = TerminalAgentApp(AgentService(factory), self.settings)
        self.addAsyncCleanup(app.session_runner.close)
        other = self.root / 'other-tui'
        other.mkdir()
        with patch('tui.AgentRuntimeFactory', return_value=candidate), \
                patch('tui.CommandController', side_effect=OSError('candidate controller failed')), \
                patch.object(app, '_append_system'):
            await TerminalAgentApp._switch_project.__wrapped__(app, str(other))
        self.assertIs(app.service.runtime_factory, factory)
        self.assertEqual(factory.checkpointer.conn.execute('SELECT 1').fetchone()[0], 1)
        self.assert_closed(candidate)

    async def test_gui_factory_close_failure_still_closes_branch_databases(self):
        from nailong.gui.controller import GUIController
        from agent_service import AgentService
        factory = self.factory_fixture()
        controller = GUIController(self.root, sessions=factory.session_store, service=AgentService(factory))
        self.addAsyncCleanup(controller.close)
        native_close = factory.aclose

        async def close():
            await native_close()
            raise OSError('GUI factory close failed')

        with patch.object(factory, 'aclose', new=close):
            with self.assertRaisesRegex(OSError, 'GUI factory close failed'):
                await controller.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            controller.branches.db.execute('SELECT 1')
        with self.assertRaises(sqlite3.ProgrammingError):
            controller.branches.view.execute('SELECT 1')

    async def test_gui_repeated_cancel_joins_actual_graph_before_closing_stores(self):
        from nailong.gui.controller import GUIController
        from agent_service import AgentService
        from test_terminal_stop_receipts import GatedModel
        model = GatedModel(responses=[], started=asyncio.Event(), cleaning=asyncio.Event(),
            release=asyncio.Event(), cleaned=asyncio.Event())
        with patch('agent.ChatDeepSeek', return_value=model):
            factory = self.factory_fixture()
        controller = GUIController(self.root, sessions=factory.session_store, service=AgentService(factory))
        self.addAsyncCleanup(controller.close)
        branch = controller.branches.create('shutdown')
        controller._enqueue({'branch_id': branch['id'], 'message': '你好', 'request_id': 'close-fixture'})
        await asyncio.wait_for(model.started.wait(), 5)
        closing = asyncio.create_task(controller.close())
        try:
            await asyncio.wait_for(model.cleaning.wait(), 2)
            for _ in range(3):
                closing.cancel()
                await asyncio.sleep(.01)
            self.assertFalse(closing.done())
            self.assertEqual(factory.checkpointer.conn.execute('SELECT 1').fetchone()[0], 1)
            self.assertEqual(controller.branches.db.execute('SELECT 1').fetchone()[0], 1)
        finally:
            model.release.set()
            results = await asyncio.gather(closing, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertTrue(model.cleaned.is_set())
        self.assert_closed(factory)
        with self.assertRaises(sqlite3.ProgrammingError):
            controller.branches.db.execute('SELECT 1')

    async def test_gui_project_switch_keeps_adopted_project_when_old_close_fails(self):
        from nailong.gui.controller import GUIController
        from agent_service import AgentService
        factory = self.factory_fixture()
        controller = GUIController(self.root, sessions=factory.session_store, service=AgentService(factory))
        self.addAsyncCleanup(controller.close)
        other = self.root / 'other-gui'
        other.mkdir()
        candidate = GUIController(other, sessions=ProjectSessionStore(other, base_dir=self.root / 'candidate-state'))
        self.addAsyncCleanup(candidate.close)
        native_close = factory.aclose

        async def close():
            await native_close()
            raise OSError('old GUI close failed fixture-key')

        with patch('nailong.gui.controller.GUIController', return_value=candidate), \
                patch.object(factory, 'aclose', new=close):
            await controller._command_unlocked('open_project', {'path': str(other)})
            with self.assertRaises(Exception) as repeated:
                await controller._command_unlocked('open_project', {'path': str(self.root)})
            self.assertNotIn('fixture-key', controller.safe(str(repeated.exception)))
        self.assertEqual(controller.project, other)
        self.assertEqual(controller.branches.db.execute('SELECT 1').fetchone()[0], 1)
        self.assertNotIn('fixture-key', controller.error)
        await controller.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            candidate.branches.db.execute('SELECT 1')

    async def test_tui_retries_failed_native_cleanup_before_loop_exit(self):
        from tui import TerminalAgentApp
        from agent_service import AgentService
        factory = self.factory_fixture()
        await factory.async_runtime(thread_id='tui-retry-close')
        saver = factory.async_checkpointer
        native_close, attempts = saver.conn.close, []

        async def close():
            attempts.append('close')
            if len(attempts) <= 2:
                raise OSError('temporary TUI close failure')
            await native_close()

        app = TerminalAgentApp(AgentService(factory), self.settings)
        with patch.object(saver.conn, 'close', new=close):
            with self.assertRaises(BaseExceptionGroup):
                await app._shutdown_runtime()
            await app._shutdown_runtime()
        with self.assertRaises(ValueError):
            await saver.conn.execute('SELECT 1')

    async def test_gui_cancelled_project_switch_retains_both_resource_owners(self):
        from nailong.gui.controller import GUIController
        from agent_service import AgentService
        factory = self.factory_fixture()
        controller = GUIController(self.root, sessions=factory.session_store, service=AgentService(factory))
        self.addAsyncCleanup(controller.close)
        other = self.root / 'cancelled-gui-switch'
        other.mkdir()
        candidate = GUIController(other, sessions=ProjectSessionStore(other, base_dir=self.root / 'cancelled-state'))
        self.addAsyncCleanup(candidate.close)
        entered, release = asyncio.Event(), asyncio.Event()
        native_close = factory.aclose

        async def close():
            entered.set()
            await release.wait()
            await native_close()

        with patch('nailong.gui.controller.GUIController', return_value=candidate), \
                patch.object(factory, 'aclose', new=close):
            operation = asyncio.create_task(controller._command_unlocked('open_project', {'path': str(other)}))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                for _ in range(3):
                    operation.cancel()
                    await asyncio.sleep(.01)
                self.assertFalse(operation.done())
                self.assertEqual(controller.project, other)
                self.assertTrue(any(event['kind'] == 'project_opened' for event in controller.events_log))
                self.assertEqual(controller.branches.db.execute('SELECT 1').fetchone()[0], 1)
            finally:
                release.set()
                results = await asyncio.gather(operation, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        await controller.close()
        self.assert_closed(factory)
        with self.assertRaises(sqlite3.ProgrammingError):
            candidate.branches.db.execute('SELECT 1')

    async def test_headless_cancelled_final_refresh_joins_before_store_close(self):
        import headless
        from agent_service import AgentService
        factory, runtime = self.headless_runtime()
        entered, release = asyncio.Event(), asyncio.Event()
        native_report = AgentService.delivery_report
        calls = []

        async def report(service, thread_id):
            calls.append(thread_id)
            if len(calls) == 2:
                entered.set()
                await release.wait()
            return await native_report(service, thread_id)

        with patch.object(headless, 'create_agent_runtime', return_value=runtime), \
                patch.object(AgentService, 'delivery_report', new=report):
            operation = asyncio.create_task(headless.run_print(self.settings, '/status',
                output_format='json', stdout=self.output))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                for _ in range(3):
                    operation.cancel()
                    await asyncio.sleep(.01)
                self.assertFalse(operation.done())
                self.assertEqual(factory.checkpointer.conn.execute('SELECT 1').fetchone()[0], 1)
            finally:
                release.set()
                results = await asyncio.gather(operation, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assert_closed(factory)
