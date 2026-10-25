"""The injected-input CLI must close resources inside its owning Runner."""
import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from agent import AgentRuntimeFactory
from config import Settings
from nailong.core.sessions import ProjectSessionStore


class LegacyCLIResourceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-chat', self.root)
        with patch('agent.ChatDeepSeek', return_value=object()):
            self.factory = AgentRuntimeFactory(self.settings,
                session_store=ProjectSessionStore(self.root, base_dir=self.root / 'state'))
        self.addCleanup(lambda: asyncio.run(self.factory.aclose()))
        self.runtime = self.factory()

    def assert_closed(self):
        with self.assertRaises(sqlite3.ProgrammingError):
            self.factory.checkpointer.conn.execute('SELECT 1')

    def test_session_list_failure_closes_factory_before_propagating(self):
        with patch('main.create_agent_runtime', return_value=self.runtime), \
                patch.object(self.factory.session_store, 'list_sessions', side_effect=OSError('CLI list failed')):
            with self.assertRaisesRegex(OSError, 'CLI list failed'):
                main.run_cli(self.settings, input_fn=lambda _: '/exit', output_fn=lambda _: None)
        self.assert_closed()

    def test_loop_failure_joins_async_close_before_runner_exits(self):
        savers = []

        def session(*args, **kwargs):
            runner = args[4]
            runner.run(self.factory.async_runtime(thread_id='legacy-cli'))
            savers.append(self.factory.async_checkpointer)
            raise OSError('late CLI failed')

        with patch('main.create_agent_runtime', return_value=self.runtime), \
                patch('main._run_cli_session', side_effect=session):
            with self.assertRaisesRegex(OSError, 'late CLI failed'):
                main.run_cli(self.settings, input_fn=lambda _: '/exit', output_fn=lambda _: None)
        self.assert_closed()
        with self.assertRaises(ValueError):
            asyncio.run(savers[0].conn.execute_fetchall('SELECT 1'))
