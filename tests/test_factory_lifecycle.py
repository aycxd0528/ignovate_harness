"""Owned runtime resources, using real SQLite and SDK clients without network."""
import asyncio
import sqlite3
import tempfile
import sys
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent import AgentRuntimeFactory, AsyncSqliteSaver, create_agent_runtime
from config import Settings
from nailong.core.sessions import ProjectSessionStore


class FactoryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-chat', self.root)
        self.store = ProjectSessionStore(self.root, base_dir=self.root / 'state')

    def factory(self):
        with patch('agent.ChatDeepSeek', return_value=object()):
            factory = AgentRuntimeFactory(self.settings, session_store=self.store)
        self.addAsyncCleanup(factory.aclose)
        return factory

    async def test_constructor_failure_closes_acquired_sqlite(self):
        connections = []
        native_connect = sqlite3.connect

        def connect(*args, **kwargs):
            connection = native_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        try:
            failure = None
            with patch('agent.ChatDeepSeek', return_value=object()), \
                    patch('sqlite3.connect', side_effect=connect), \
                    patch('nailong.core.runtime_options.child_options', side_effect=OSError('bad options')):
                try:
                    AgentRuntimeFactory(self.settings, session_store=self.store)
                except OSError as error:
                    failure = error  # Real callers/loggers may retain the traceback and partial factory.
            self.assertIsInstance(failure, OSError)
            self.assertEqual(len(connections), 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                connections[0].execute('SELECT 1')
        finally:
            for connection in connections:
                connection.close()

    async def test_concurrent_first_runtimes_share_one_native_connection(self):
        factory = self.factory()
        entered, release = asyncio.Event(), asyncio.Event()
        savers = []
        native = AsyncSqliteSaver.from_conn_string

        @asynccontextmanager
        async def opening(*args, **kwargs):
            async with native(*args, **kwargs) as saver:
                savers.append(saver)
                entered.set()
                await release.wait()
                yield saver

        operations = []
        try:
            with patch.object(AsyncSqliteSaver, 'from_conn_string', side_effect=opening):
                operations.append(asyncio.create_task(factory.async_runtime(thread_id='first')))
                await asyncio.wait_for(entered.wait(), 2)
                operations.append(asyncio.create_task(factory.async_runtime(thread_id='second')))
                for _ in range(10):
                    await asyncio.sleep(0.01)
                release.set()
                await asyncio.gather(*operations)
            self.assertEqual(len(savers), 1, 'concurrent startup must not orphan a second native saver')
        finally:
            release.set()
            await asyncio.gather(*operations, return_exceptions=True)
            await factory.aclose()
            for saver in savers:
                await saver.conn.close()

    async def test_setup_failure_rolls_back_before_publishing_and_can_retry(self):
        factory = self.factory()
        savers = []
        native_setup = AsyncSqliteSaver.setup

        async def setup(saver):
            savers.append(saver)
            if len(savers) == 1:
                raise OSError('setup failure')
            await native_setup(saver)

        with patch.object(AsyncSqliteSaver, 'setup', new=setup):
            with self.assertRaisesRegex(OSError, 'setup failure'):
                await factory.async_runtime(thread_id='bad-setup')
            self.assertIsNone(factory.async_checkpointer, 'failed saver must never be published')
            with self.assertRaises(ValueError):
                await savers[0].conn.execute('SELECT 1')
            await factory.async_runtime(thread_id='retry-setup')
        self.assertEqual(len(savers), 2)
        self.assertEqual((await factory.async_checkpointer.conn.execute_fetchall('SELECT 1'))[0][0], 1)

    async def test_close_joins_opening_and_seals_new_admission(self):
        factory = self.factory()
        entered, release = asyncio.Event(), asyncio.Event()
        native_setup = AsyncSqliteSaver.setup
        savers = []

        async def setup(saver):
            savers.append(saver)
            entered.set()
            await release.wait()
            await native_setup(saver)

        operations = []
        try:
            with patch.object(AsyncSqliteSaver, 'setup', new=setup):
                operations.append(asyncio.create_task(factory.async_runtime(thread_id='opening')))
                await asyncio.wait_for(entered.wait(), 2)
                operations.append(asyncio.create_task(factory.aclose()))
                await asyncio.sleep(0.03)
                self.assertFalse(operations[-1].done(), 'close must join in-flight SQLite initialization')
                with self.assertRaisesRegex(RuntimeError, 'closed|closing|关闭'):
                    factory(thread_id='too-late')
        finally:
            release.set()
            await asyncio.gather(*operations, return_exceptions=True)
        self.assertIsNone(factory.async_checkpointer)
        with self.assertRaises(ValueError):
            await savers[0].conn.execute('SELECT 1')

    async def test_repeated_cancel_of_close_waiter_joins_checkpoint_cleanup(self):
        factory = self.factory()
        await factory.async_runtime(thread_id='write')
        saver = factory.async_checkpointer
        await saver.lock.acquire()
        closing = asyncio.create_task(factory.aclose())
        try:
            await asyncio.sleep(0.01)
            closing.cancel('first')
            await asyncio.sleep(0.01)
            closing.cancel('again')
            await asyncio.sleep(0.01)
            self.assertFalse(closing.done(), 'cancelled controller must still own the cleanup join')
            self.assertIs(factory.async_checkpointer, saver)
            self.assertEqual(factory.checkpointer.conn.execute('SELECT 1').fetchone()[0], 1)
        finally:
            saver.lock.release()
            results = await asyncio.gather(closing, return_exceptions=True)
            await factory.aclose()
        self.assertIsInstance(results[0], asyncio.CancelledError)
        with self.assertRaises(ValueError):
            await saver.conn.execute('SELECT 1')

    async def test_budget_close_failure_does_not_skip_other_budget_or_sqlite(self):
        factory = self.factory()
        closed = []

        def fail():
            closed.append('first')
            raise OSError('budget close failed')

        factory._session_token_budgets = {
            'first': SimpleNamespace(close=fail),
            'second': SimpleNamespace(close=lambda: closed.append('second')),
        }
        try:
            with self.assertRaisesRegex(OSError, 'budget close failed'):
                factory.close()
            self.assertEqual(closed, ['first', 'second'])
            with self.assertRaises(sqlite3.ProgrammingError):
                factory.checkpointer.conn.execute('SELECT 1')
        finally:
            factory._session_token_budgets = {}
            factory.close()

    async def test_owned_model_clients_including_retired_model_are_closed(self):
        factory = AgentRuntimeFactory(self.settings, session_store=self.store)
        self.addAsyncCleanup(factory.aclose)
        original = factory.model
        factory.set_model('deepseek-reasoner')
        replacement = factory.model
        self.assertFalse(original.root_async_client.is_closed())
        try:
            await factory.aclose()
            for model in (original, replacement):
                self.assertTrue(model.root_client.is_closed())
                self.assertTrue(model.root_async_client.is_closed())
        finally:
            for model in (original, replacement):
                model.root_client.close()
                await model.root_async_client.close()

    async def test_sync_only_factory_closes_unused_async_sdk_client(self):
        factory = AgentRuntimeFactory(self.settings, session_store=self.store)
        self.addAsyncCleanup(factory.aclose)
        try:
            factory.close()
            self.assertTrue(factory.model.root_client.is_closed())
            self.assertTrue(factory.model.root_async_client.is_closed())
        finally:
            factory.model.root_client.close()
            await factory.model.root_async_client.close()

    async def test_reasoning_model_is_owned_and_closed_factory_rejects_rebuild(self):
        factory = AgentRuntimeFactory(self.settings, session_store=self.store)
        self.addAsyncCleanup(factory.aclose)
        factory.set_reasoning('default')
        replacement = factory.model
        try:
            await factory.aclose()
            self.assertTrue(replacement.root_client.is_closed())
            self.assertTrue(replacement.root_async_client.is_closed())
            with self.assertRaises(RuntimeError):
                factory.set_reasoning('default')
        finally:
            replacement.root_client.close()
            await replacement.root_async_client.close()

    async def test_child_compile_failure_closes_private_model_before_factory_exit(self):
        factory = AgentRuntimeFactory(self.settings, session_store=self.store)
        self.addAsyncCleanup(factory.aclose)
        from agent import ChatDeepSeek
        children = []

        def model(*args, **kwargs):
            child = ChatDeepSeek(*args, **kwargs)
            children.append(child)
            return child

        try:
            with patch('agent.ChatDeepSeek', side_effect=model), \
                    patch('agent.create_agent', side_effect=OSError('child compile failed')):
                with self.assertRaisesRegex(OSError, 'child compile failed'):
                    await factory._run_readonly_subagent('local child')
            self.assertEqual(len(children), 1)
            self.assertTrue(children[0].root_client.is_closed())
            self.assertTrue(children[0].root_async_client.is_closed())
            self.assertFalse(factory.model.root_async_client.is_closed())
        finally:
            for child in children:
                child.root_client.close()
                await child.root_async_client.close()

    async def test_failed_initialization_rollback_remains_owned_for_close_retry(self):
        factory = self.factory()
        native = AsyncSqliteSaver.from_conn_string
        savers, attempts = [], []

        class Context:
            def __init__(self, *args):
                self.inner = native(*args)

            async def __aenter__(self):
                saver = await self.inner.__aenter__()
                savers.append(saver)
                return saver

            async def __aexit__(self, *args):
                attempts.append('exit')
                if len(attempts) == 1:
                    raise OSError('transient close failure')
                return await self.inner.__aexit__(*args)

        try:
            with patch.object(AsyncSqliteSaver, 'from_conn_string', side_effect=Context), \
                    patch.object(AsyncSqliteSaver, 'setup', side_effect=OSError('bad setup')):
                with self.assertRaises(BaseExceptionGroup):
                    await factory.async_runtime(thread_id='failed-rollback')
            await factory.aclose()
            self.assertEqual(len(attempts), 2, 'unpublished resources still belong to the factory')
            with self.assertRaises(ValueError):
                await savers[0].conn.execute('SELECT 1')
        finally:
            for saver in savers:
                await saver.conn.close()

    async def test_sync_close_rejects_live_mcp_without_losing_connection_owner(self):
        factory = self.factory()
        server = Path(__file__).resolve().parent / 'fixtures/mcp_server.py'
        factory.mcp_manager.store.add('local', {'transport': 'stdio', 'command': sys.executable,
            'args': [str(server)], 'timeout_seconds': 5})
        await factory.mcp_manager.connect('local')
        connection = factory.mcp_manager.connection_identity('local')
        with self.assertRaisesRegex(RuntimeError, 'aclose'):
            factory.close()
        await factory.aclose()
        self.assertTrue(connection.task.done())

    async def test_native_connection_close_can_retry_after_context_is_exhausted(self):
        factory = self.factory()
        await factory.async_runtime(thread_id='retry-native-close')
        saver = factory.async_checkpointer
        native_close = saver.conn.close
        attempts = []

        async def close():
            attempts.append('close')
            if len(attempts) == 1:
                raise OSError('native close temporarily failed')
            await native_close()

        with patch.object(saver.conn, 'close', new=close):
            with self.assertRaisesRegex(OSError, 'native close temporarily failed'):
                await factory.aclose()
            await factory.aclose()
        try:
            with self.assertRaises(ValueError):
                await saver.conn.execute('SELECT 1')
        finally:
            await native_close()

    async def test_sync_native_close_retries_after_context_exit_failure(self):
        class Connection(sqlite3.Connection):
            attempts = 0

            def close(self):
                self.attempts += 1
                if self.attempts == 1:
                    raise OSError('sync native close temporarily failed')
                return super().close()

        native_connect = sqlite3.connect
        with patch('sqlite3.connect', side_effect=lambda *a, **kw: native_connect(*a, **kw, factory=Connection)):
            factory = self.factory()
        connection = factory.checkpointer.conn
        try:
            with self.assertRaisesRegex(OSError, 'sync native close temporarily failed'):
                factory.close()
            factory.close()
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute('SELECT 1')
        finally:
            connection.close()

    async def test_shared_child_model_survives_model_switch_until_factory_close(self):
        from agent import ChatDeepSeek
        native_model = ChatDeepSeek(model='deepseek-chat', api_key='fixture-key', base_url='https://api.invalid')
        shared = SimpleNamespace(root_client=native_model.root_client,
            root_async_client=native_model.root_async_client, max_tokens=1000)
        entered, release = asyncio.Event(), asyncio.Event()

        async def invoke(*args, **kwargs):
            entered.set()
            await release.wait()
            return {'messages': []}

        with patch('agent.ChatDeepSeek', return_value=shared):
            factory = AgentRuntimeFactory(self.settings, session_store=self.store)
        self.addAsyncCleanup(factory.aclose)
        operation = None
        try:
            with patch('agent.create_agent', return_value=SimpleNamespace(ainvoke=invoke)):
                operation = asyncio.create_task(factory._run_readonly_subagent('shared model'))
                await asyncio.wait_for(entered.wait(), 2)
                factory.set_model('deepseek-reasoner')
                release.set()
                await operation
            self.assertFalse(shared.root_client.is_closed(), 'a shared old graph may still use this model')
            self.assertFalse(shared.root_async_client.is_closed())
        finally:
            release.set()
            if operation is not None:
                await asyncio.gather(operation, return_exceptions=True)
            await factory.aclose()

    async def test_create_runtime_compile_failure_releases_factory(self):
        factory = self.factory()
        with patch('agent.AgentRuntimeFactory', return_value=factory), \
                patch.object(AgentRuntimeFactory, '_build_runtime', side_effect=OSError('compile failed')):
            with self.assertRaisesRegex(OSError, 'compile failed'):
                create_agent_runtime(self.settings)
        with self.assertRaises(sqlite3.ProgrammingError):
            factory.checkpointer.conn.execute('SELECT 1')
