from platform_fixtures import process_exists
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import Settings
from ui.actions import CommandActions
from ui.controller import CommandController

SERVER = Path(__file__).parent / 'fixtures/mcp_server.py'


class MCPCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.settings = Settings('key', 'https://api.invalid', 'deepseek-flash', self.root)
        self.controller = CommandController(self.settings)
        self.actions = CommandActions(self.controller)
        from nailong.mcp.client import MCPManager
        from nailong.mcp.config import MCPConfigStore
        self.manager = MCPManager(MCPConfigStore(self.root))
        self.addAsyncCleanup(self.manager.aclose)
        self.service = SimpleNamespace(runtime_factory=SimpleNamespace(settings=self.settings, mcp_manager=self.manager),
                                       session_store=None, permission_mode='default')

    async def command(self, message):
        try:
            request = self.controller.resolve(message)
        except ValueError:
            self.fail('/mcp command is not registered')
        self.assertTrue(self.actions.handles(request), 'MCP actions must be shared by all UIs')
        result = await self.actions.execute(request, self.service, 'thread')
        self.assertFalse(result.model_requests)
        return result

    async def test_add_connect_tools_disconnect_remove_are_local_commands(self):
        result = await self.command('/mcp')
        self.assertIn('尚未配置', result.text)
        await self.command('/mcp add test --transport stdio -- ' + shlex.join([sys.executable, str(SERVER)]))
        result = await self.command('/mcp list')
        self.assertIn('test', result.text)
        self.assertEqual(self.manager.status('test')['status'], 'disconnected')
        result = await self.command('/mcp connect test')
        self.assertIn('connected', result.text)
        result = await self.command('/mcp tools test')
        self.assertIn('mcp__test__echo', result.text)
        await self.command('/mcp disconnect test')
        self.assertEqual(self.manager.tools('test'), ())
        await self.command('/mcp connect test')
        await self.command('/mcp remove test')
        self.assertEqual(self.manager.tools('test'), ())
        self.assertEqual(self.manager.store.list_servers(), {})

    async def test_http_add_and_usage_errors_preserve_existing_configuration(self):
        await self.command('/mcp add docs --transport http https://example.com/mcp')
        self.assertEqual(self.manager.store.list_servers()['docs']['url'], 'https://example.com/mcp')
        for argument in ('add bad --transport ftp x', 'remove', 'connect missing', 'tools docs extra', 'list extra'):
            with self.subTest(argument=argument):
                with self.assertRaises(ValueError):
                    await self.command('/mcp ' + argument)
        self.assertEqual(set(self.manager.store.list_servers()), {'docs'})

    async def test_windows_mcp_command_keeps_quoted_and_unquoted_backslash_paths(self):
        with patch('ui.arguments._WINDOWS', True):
            await self.command(r'/mcp add native --transport stdio -- C:\Tools\python.exe "C:\项目 文件\server.py"')
        definition = self.manager.store.list_servers()['native']
        self.assertEqual(definition['command'], r'C:\Tools\python.exe')
        self.assertEqual(definition['args'], [r'C:\项目 文件\server.py'])

    async def test_windows_mcp_arguments_keep_embedded_quotes_empty_values_and_trailing_slashes(self):
        with patch('ui.arguments._WINDOWS', True):
            await self.command(r'/mcp add quoted --transport stdio -- python.exe -c "print(\"hi\")" "" "C:\尾\\"')
        definition = self.manager.store.list_servers()['quoted']
        self.assertEqual(definition['args'], ['-c', 'print("hi")', '', 'C:\\尾\\'])

    async def test_plan_mode_allows_listing_but_refuses_connection(self):
        await self.command('/mcp add docs --transport http https://example.com/mcp')
        self.service.permission_mode = 'plan'
        self.assertIn('docs', (await self.command('/mcp')).text)
        with self.assertRaises(ValueError):
            await self.command('/mcp connect docs')

    async def test_textual_dispatch_uses_shared_management_actions(self):
        from tui import TerminalAgentApp
        from ui.transcript import TranscriptLog
        app = TerminalAgentApp(self.service, self.settings)
        async with app.run_test(size=(100, 32)) as pilot:
            app._dispatch('/mcp')
            await pilot.pause()
            await app.session_runner.wait_idle()
            transcript = app.query_one('#transcript', TranscriptLog)
            self.assertIn('尚未配置', '\n'.join(line.text for line in transcript.lines))


class MCPProjectSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def test_textual_project_switch_closes_previous_service_process(self):
        from agent import AgentRuntimeFactory
        from agent_service import AgentService
        from nailong.core.sessions import ProjectSessionStore
        from tui import TerminalAgentApp
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as data:
            root = Path(directory).resolve()
            other = root / 'other'
            other.mkdir()
            settings = Settings('test', 'https://api.invalid', 'deepseek-flash', root)
            factory = AgentRuntimeFactory(settings, session_store=ProjectSessionStore(root, base_dir=data))
            self.addCleanup(factory.close)
            self.addAsyncCleanup(factory.aclose)
            manager = factory.mcp_manager
            manager.store.add('local', {'transport': 'stdio', 'command': sys.executable, 'args': [str(SERVER)]})
            await manager.connect('local')
            pid = (await manager.call_tool('local', 'environment', {}))['structured_content']['pid']
            with patch.dict(os.environ, {'NAILONG_DATA_DIR': data}):
                app = TerminalAgentApp(AgentService(factory, session_store=factory.session_store), settings)
                async with app.run_test(size=(100, 32)) as pilot:
                    app._dispatch('/project ' + str(other))
                    await pilot.pause()
                    await app.workers.wait_for_complete()
                    self.assertEqual(app.settings.project_root, other)
                    self.assertEqual(manager.tools('local'), ())
                    self.assertFalse(process_exists(pid))


if __name__ == '__main__':
    unittest.main()
