import asyncio
import importlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SERVER = Path(__file__).parent / 'fixtures/mcp_server.py'


class MCPClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        from nailong.mcp.config import MCPConfigStore
        self.store = MCPConfigStore(self.root)
        try:
            cls = importlib.import_module('nailong.mcp.client').MCPManager
        except ModuleNotFoundError:
            self.fail('MCP connection manager is not implemented')
        self.manager = cls(self.store, api_key='provider-secret')
        self.addAsyncCleanup(self.manager.aclose)
        self.store.add('local', {'transport': 'stdio', 'command': sys.executable,
                               'args': [str(SERVER)], 'timeout_seconds': 5})

    async def test_stdio_discovery_nested_call_error_and_reconnect(self):
        await self.manager.connect('local')
        self.assertEqual(self.manager.status('local')['status'], 'connected')
        self.assertIn('echo', [item.name for item in self.manager.tools('local')])
        value = await self.manager.call_tool('local', 'echo', {'payload': {'items': [1, {'nested': True}]}})
        self.assertTrue(value['ok'], value)
        self.assertEqual(value['structured_content']['payload'], {'items': [1, {'nested': True}]})
        error = await self.manager.call_tool('local', 'fail', {})
        self.assertFalse(error['ok'])
        self.assertEqual(error['error_code'], 'mcp_tool_error')
        await self.manager.disconnect('local')
        self.assertEqual(self.manager.tools('local'), ())
        await self.manager.connect('local')
        self.assertTrue((await self.manager.call_tool('local', 'echo', {'payload': {}}))['ok'])

    async def test_credential_redaction_and_provider_key_not_inherited(self):
        self.store.add('auth', {'transport': 'stdio', 'command': sys.executable,
                               'args': [str(SERVER)], 'env': {'MCP_TEST_TOKEN': '${MCP_PRIVATE}'}})
        with patch.dict(os.environ, {'MCP_PRIVATE': 'private-token', 'DEEPSEEK_API_KEY': 'provider-secret'}):
            await self.manager.connect('auth')
        result = await self.manager.call_tool('auth', 'environment', {})
        self.assertTrue(result['ok'], result)
        self.assertNotIn('private-token', json.dumps(result))
        self.assertFalse(result['structured_content']['deepseek_present'])
        self.assertNotIn('private-token', json.dumps(self.manager.sanitize({'private-token': 'value'})))

    async def test_tool_names_containing_transport_credentials_are_not_registered(self):
        self.store.add('unsafe', {'transport':'stdio', 'command':sys.executable,
            'args':[str(SERVER), '--secret-tool-name'], 'env':{'MCP_TEST_TOKEN':'${MCP_PRIVATE}'}})
        with patch.dict(os.environ, {'MCP_PRIVATE':'private-token-sentinel'}):
            with self.assertRaises(ValueError) as error:
                await self.manager.connect('unsafe')
        self.assertNotIn('private-token-sentinel', str(error.exception))
        self.assertEqual(self.manager.tools('unsafe'), ())

    async def test_cancel_closes_connection_and_child_process(self):
        await self.manager.connect('local')
        pid = (await self.manager.call_tool('local', 'environment', {}))['structured_content']['pid']
        call = asyncio.create_task(self.manager.call_tool('local', 'pause', {'seconds': 20}))
        await asyncio.sleep(.1)
        call.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await call
        self.assertEqual(self.manager.tools('local'), ())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    async def test_timeout_closes_connection_without_call_replay(self):
        self.store.add('short', {'transport': 'stdio', 'command': sys.executable,
                               'args': [str(SERVER)], 'timeout_seconds': 1})
        await self.manager.connect('short')
        value = await self.manager.call_tool('short', 'pause', {'seconds': 5})
        self.assertFalse(value['ok'])
        self.assertTrue(value['timed_out'])
        self.assertEqual(self.manager.tools('short'), ())

    async def test_explicit_disconnect_resolves_an_active_call_promptly(self):
        await self.manager.connect('local')
        call = asyncio.create_task(self.manager.call_tool('local', 'pause', {'seconds': 20}))
        await asyncio.sleep(.1)
        await self.manager.disconnect('local')
        value = await asyncio.wait_for(call, .5)
        self.assertFalse(value['ok'])
        self.assertEqual(value['error_code'], 'mcp_disconnected')

    async def test_cancelled_old_call_does_not_close_reconnected_service(self):
        await self.manager.connect('local')
        call = asyncio.create_task(self.manager.call_tool('local', 'pause', {'seconds': 20}))
        await asyncio.sleep(.1)
        await self.manager.disconnect('local')
        await self.manager.connect('local')
        call.cancel()
        try:
            await call
        except asyncio.CancelledError:
            pass
        self.assertEqual(self.manager.status('local')['status'], 'connected')

    async def test_invalid_executable_and_missing_credentials_do_not_leave_tools(self):
        self.store.add('missing', {'transport': 'stdio', 'command': '/no/such/executable'})
        with self.assertRaises(ValueError):
            await self.manager.connect('missing')
        self.assertEqual(self.manager.status('missing')['status'], 'error')
        self.store.add('auth', {'transport': 'http', 'url': 'http://127.0.0.1:1/mcp',
                               'headers': {'Authorization': '${NAILONG_TEST_ABSENT}'}})
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                await self.manager.connect('auth')
        self.assertEqual(self.manager.tools('auth'), ())

    async def test_pagination_discovers_all_tools_and_rejects_invalid_servers(self):
        script = SERVER.with_name('mcp_paged_server.py')
        for mode in ('paged', 'cycle', 'duplicate', 'remote-ref', 'anchor', 'collision'):
            self.store.add(mode, {'transport': 'stdio', 'command': sys.executable, 'args': [str(script), mode]})
            if mode == 'paged':
                await self.manager.connect(mode)
                self.assertEqual([item.name for item in self.manager.tools(mode)], ['first', 'second'])
            else:
                with self.subTest(mode=mode):
                    with self.assertRaises(ValueError):
                        await self.manager.connect(mode)
                    self.assertEqual(self.manager.tools(mode), ())

    async def test_streamable_http_round_trip_and_shutdown(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        process = subprocess.Popen([sys.executable, str(SERVER), '--http', '--port', str(port)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(process.wait, 5)
        self.addCleanup(process.terminate)
        for _ in range(100):
            try:
                reader, writer = await asyncio.open_connection('127.0.0.1', port)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                await asyncio.sleep(.05)
        else:
            self.fail('Local HTTP test server did not start')
        self.store.add('http', {'transport': 'http', 'url': f'http://127.0.0.1:{port}/mcp'})
        await self.manager.connect('http')
        result = await self.manager.call_tool('http', 'echo', {'payload': {'transport': 'http'}})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['structured_content']['payload'], {'transport': 'http'})
        await self.manager.aclose()
        self.assertEqual(self.manager.tools('http'), ())


if __name__ == '__main__':
    unittest.main()
