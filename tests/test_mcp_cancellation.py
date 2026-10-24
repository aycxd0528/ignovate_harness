"""Actual local MCP execution: cancellation must respect request admission."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nailong.mcp.client import MCPManager
from nailong.mcp.config import MCPConfigStore
from platform_fixtures import process_exists

SERVER = Path(__file__).resolve().parent / 'fixtures/mcp_server.py'


class MCPCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.store = MCPConfigStore(self.root)
        self.store.add('local', {'transport': 'stdio', 'command': sys.executable,
                               'args': [str(SERVER)], 'timeout_seconds': 5})
        self.manager = MCPManager(self.store)
        self.addAsyncCleanup(self.manager.aclose)
        await self.manager.connect('local')
        self.connection = self.manager.connection_identity('local')

    async def wait_until(self, condition):
        async with asyncio.timeout(3):
            while not condition():
                await asyncio.sleep(.005)

    async def hold(self):
        started, release, completed = (self.root / name for name in ('started', 'release', 'completed'))
        call = asyncio.create_task(self.manager.call_tool('local', 'hold', {
            'started': str(started), 'release': str(release), 'completed': str(completed)}))
        self.addAsyncCleanup(self.finish, call)
        await self.wait_until(started.exists)
        return call, release, completed

    @staticmethod
    async def finish(task):
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_cancel_queued_request_keeps_running_call_and_connection(self):
        # Missing queued/started distinction wrongly cancels another tool's side effect.
        active, release, completed = await self.hold()
        effect = self.root / 'must-not-run'
        queued = asyncio.create_task(self.manager.call_tool('local', 'touch', {'path': str(effect)}))
        await self.wait_until(lambda: self.connection.queue.qsize() == 1)
        queued.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued
        release.touch()
        value = await active
        self.assertTrue(value['ok'], value)
        self.assertTrue(completed.exists())
        self.assertEqual(self.manager.status('local')['status'], 'connected')
        self.assertTrue((await self.manager.call_tool('local', 'echo', {'payload': {}}))['ok'])
        self.assertFalse(effect.exists())

    async def test_queue_timeout_reports_not_started_without_disconnect(self):
        # Queue wait time counts against the request, not another active operation.
        active, release, completed = await self.hold()
        self.connection.definition['timeout_seconds'] = 1
        effect = self.root / 'must-not-run'
        value = await self.manager.call_tool('local', 'touch', {'path': str(effect)})
        release.touch()
        active_value = await active
        self.assertTrue(active_value['ok'], active_value)
        self.assertTrue(completed.exists())
        self.assertEqual(value['error_code'], 'mcp_timeout')
        self.assertTrue(value['timed_out'])
        self.assertIs(value.get('started'), False)
        self.assertEqual(self.manager.status('local')['status'], 'connected')
        self.assertFalse(effect.exists())

    async def test_cancel_queued_requests_immediately_releases_capacity(self):
        # Canceled entries cannot exhaust the 16 pending requests for a long call.
        active, release, _ = await self.hold()
        for index in range(18):
            queued = asyncio.create_task(self.manager.call_tool('local', 'touch', {
                'path': str(self.root / f'forbidden-{index}')}))
            await self.wait_until(lambda: self.connection.queue.qsize() == 1 or queued.done())
            self.assertFalse(queued.done(), f'capacity was lost at {index}')
            queued.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await queued
            self.assertEqual(self.connection.queue.qsize(), 0)
        release.touch()
        self.assertTrue((await active)['ok'])
        self.assertFalse(list(self.root.glob('forbidden-*')))

    async def gate_teardown(self):
        # Keep the real SDK/process. Only delay its cleanup at the transport
        # boundary, inside the same Task that entered the SDK contexts.
        from mcp.client.stdio import stdio_client
        await self.manager.disconnect('local')
        entered, release = asyncio.Event(), asyncio.Event()
        interruptions = []

        @asynccontextmanager
        async def gated(*args, **kwargs):
            async with stdio_client(*args, **kwargs) as streams:
                try:
                    yield streams
                finally:
                    entered.set()
                    while not release.is_set():
                        try:
                            await release.wait()
                        except asyncio.CancelledError:
                            interruptions.append('cleanup was cancelled again')

        replacement = patch('mcp.client.stdio.stdio_client', gated)
        replacement.start()
        self.addCleanup(replacement.stop)
        self.addCleanup(release.set)
        await self.manager.connect('local')
        self.connection = self.manager.connection_identity('local')
        return entered, release, interruptions

    async def test_concurrent_active_cancel_and_disconnect_do_not_recancel_owner(self):
        # Without a close latch, the second controller cancels SDK teardown.
        entered, release, interruptions = await self.gate_teardown()
        active, _, _ = await self.hold()
        active.cancel()
        await asyncio.wait_for(entered.wait(), 3)
        disconnect = asyncio.create_task(self.manager.disconnect('local'))
        try:
            await asyncio.sleep(0)
            for _ in range(3):
                active.cancel()
                await asyncio.sleep(0)
            self.assertFalse(active.done())
            self.assertFalse(disconnect.done())
            self.assertEqual(interruptions, [])
        finally:
            release.set()
            await asyncio.gather(active, disconnect, return_exceptions=True)
        self.assertEqual(self.manager.status('local')['status'], 'disconnected')

    async def test_cancel_disconnect_waits_cleanup_then_propagates_cancel(self):
        # Swallowing the controller's cancellation makes shutdown look successful.
        entered, release, _ = await self.gate_teardown()
        pid = (await self.manager.call_tool('local', 'environment', {}))['structured_content']['pid']
        disconnect = asyncio.create_task(self.manager.disconnect('local'))
        await asyncio.wait_for(entered.wait(), 3)
        try:
            for _ in range(3):
                disconnect.cancel()
                await asyncio.sleep(0)
            self.assertFalse(disconnect.done())
            self.assertTrue(process_exists(pid))
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await disconnect
        self.assertFalse(process_exists(pid))

    async def test_closing_connection_stops_new_admission_before_sdk_teardown(self):
        # Discovery and a new tool call must not describe a closing owner as usable.
        entered, release, _ = await self.gate_teardown()
        disconnect = asyncio.create_task(self.manager.disconnect('local'))
        await asyncio.wait_for(entered.wait(), 3)
        try:
            self.assertEqual(self.manager.tools('local'), ())
            value = await asyncio.wait_for(self.manager.call_tool('local', 'echo', {'payload': {}}), .2)
            self.assertEqual(value['error_code'], 'mcp_disconnected')
            self.assertIs(value['started'], False)
            self.assertEqual(self.connection.queue.qsize(), 0)
        finally:
            release.set()
            await disconnect

    async def test_cancel_aclose_cleans_every_connection_then_propagates(self):
        # Closing the first server must not lose the rest of a factory's resources.
        entered, release, _ = await self.gate_teardown()
        self.store.add('second', {'transport': 'stdio', 'command': sys.executable,
                                 'args': [str(SERVER)], 'timeout_seconds': 5})
        await self.manager.connect('second')
        pids = [(await self.manager.call_tool(name, 'environment', {}))['structured_content']['pid']
                for name in ('local', 'second')]
        close = asyncio.create_task(self.manager.aclose())
        await asyncio.wait_for(entered.wait(), 3)
        try:
            for _ in range(3):
                close.cancel()
                await asyncio.sleep(0)
            self.assertFalse(close.done())
        finally:
            release.set()
        result = (await asyncio.gather(close, return_exceptions=True))[0]
        self.assertFalse(any(process_exists(pid) for pid in pids))
        self.assertEqual(self.manager.tools(), ())
        self.assertIsInstance(result, asyncio.CancelledError)

    async def test_cancel_aclose_while_lifecycle_busy_still_owns_shutdown(self):
        # A close admitted while connect/disconnect holds the lock must not
        # disappear on cancellation and leave factory resources running.
        pid = (await self.manager.call_tool('local', 'environment', {}))['structured_content']['pid']
        async with self.manager._lifecycle_lock:
            close = asyncio.create_task(self.manager.aclose())
            await asyncio.sleep(0)
            close.cancel()
            await asyncio.sleep(0)
            returned_early = close.done()
        result = (await asyncio.gather(close, return_exceptions=True))[0]
        self.assertFalse(returned_early)
        self.assertIsInstance(result, asyncio.CancelledError)
        self.assertFalse(process_exists(pid))

    async def test_disconnect_cancellation_while_lifecycle_busy_joins_its_owner(self):
        # A different server's connect lock cannot leave this accepted stop
        # cancellable before it even takes ownership of its own connection.
        entered, release, _ = await self.gate_teardown()
        pid = (await self.manager.call_tool('local', 'environment', {}))['structured_content']['pid']
        async with self.manager._lifecycle_lock:
            disconnect = asyncio.create_task(self.manager.disconnect('local'))
            await asyncio.sleep(0)
            disconnect.cancel()
            await asyncio.sleep(0)
            returned_early = disconnect.done()
            tools_during_close = self.manager.tools('local')
            release.set()
        result = (await asyncio.gather(disconnect, return_exceptions=True))[0]
        self.assertFalse(returned_early)
        self.assertEqual(tools_during_close, ())
        self.assertIsInstance(result, asyncio.CancelledError)
        self.assertTrue(entered.is_set())
        self.assertFalse(process_exists(pid))

    async def test_cancel_second_disconnect_waits_first_controller_cleanup(self):
        # All callers join the captured SDK generation, including a controller
        # that arrives while the first disconnect is already cleaning up.
        entered, release, _ = await self.gate_teardown()
        first = asyncio.create_task(self.manager.disconnect('local'))
        await asyncio.wait_for(entered.wait(), 3)
        second = asyncio.create_task(self.manager.disconnect('local'))
        await asyncio.sleep(0)
        second.cancel()
        await asyncio.sleep(0)
        returned_early = second.done()
        release.set()
        outcomes = await asyncio.gather(first, second, return_exceptions=True)
        self.assertFalse(returned_early)
        self.assertIsNone(outcomes[0])
        self.assertIsInstance(outcomes[1], asyncio.CancelledError)

    async def test_shutdown_blocks_reconnect_waiting_for_old_teardown(self):
        # connect must recheck shutdown after joining an old generation.
        entered, release, _ = await self.gate_teardown()
        active, _, _ = await self.hold()
        old = self.connection
        active.cancel()
        await asyncio.wait_for(entered.wait(), 3)
        reconnect = asyncio.create_task(self.manager.connect('local'))
        await self.wait_until(self.manager._lifecycle_lock.locked)
        close = asyncio.create_task(self.manager.aclose())
        await asyncio.sleep(0)
        release.set()
        outcomes = await asyncio.gather(active, reconnect, close, return_exceptions=True)
        self.assertIsInstance(outcomes[1], ValueError)
        self.assertIs(self.manager._connections['local'], old)
        self.assertEqual(self.manager.status('local')['status'], 'disconnected')

    async def test_cancel_before_admission_does_not_wait_for_caller_wakeup(self):
        # The owner may finish one response before the cancelled caller resumes
        # to cancel its result Future. Admission must see the pending cancel.
        active, release, _ = await self.hold()
        effect = self.root / 'must-not-run'
        queued = asyncio.create_task(self.manager.call_tool('local', 'touch', {'path': str(effect)}))
        await self.wait_until(lambda: self.connection.queue.qsize() == 1)
        original = self.manager._result

        def cancel_at_boundary(response):
            value = original(response)
            queued.cancel()
            return value

        with patch.object(self.manager, '_result', cancel_at_boundary):
            release.touch()
            self.assertTrue((await active)['ok'])
            with self.assertRaises(asyncio.CancelledError):
                await queued
        self.assertEqual(self.manager.status('local')['status'], 'connected')
        self.assertTrue((await self.manager.call_tool('local', 'echo', {'payload': {}}))['ok'])
        self.assertFalse(effect.exists())

    async def test_expired_request_is_not_admitted_before_timeout_callback_runs(self):
        # A busy event loop cannot make an expired queue entry executable just
        # because asyncio's timeout callback has not had its turn yet.
        active, release, _ = await self.hold()
        self.connection.definition['timeout_seconds'] = 1
        effect = self.root / 'must-not-run'
        queued = asyncio.create_task(self.manager.call_tool('local', 'touch', {'path': str(effect)}))
        await self.wait_until(lambda: self.connection.queue.qsize() == 1)
        original = self.manager._result

        def stall_result_delivery(response):
            value = original(response)
            time.sleep(1.05)
            return value

        with patch.object(self.manager, '_result', stall_result_delivery):
            release.touch()
            self.assertTrue((await active)['ok'])
            value = await queued
        self.assertEqual(value['error_code'], 'mcp_timeout')
        self.assertIs(value.get('started'), False)
        self.assertTrue(value['timed_out'])
        self.assertEqual(self.manager.status('local')['status'], 'connected')
        self.assertTrue((await self.manager.call_tool('local', 'echo', {'payload': {}}))['ok'])
        self.assertFalse(effect.exists())

    async def test_shutdown_during_native_transport_startup_joins_connect(self):
        # Shutdown must wake ready and join its captured generation even when
        # connect still owns the lifecycle lock and has not initialized MCP.
        from mcp.client import stdio
        await self.manager.disconnect('local')
        original_transport = stdio.stdio_client
        original_spawn = stdio._create_platform_compatible_process
        entered = asyncio.Event()
        pids = []

        async def spawn(*args, **kwargs):
            process = await original_spawn(*args, **kwargs)
            pids.append(process.pid)
            return process

        @asynccontextmanager
        async def starting(*args, **kwargs):
            async with original_transport(*args, **kwargs) as streams:
                entered.set()
                await asyncio.Event().wait()
                yield streams

        with patch.object(stdio, 'stdio_client', starting), patch.object(
                stdio, '_create_platform_compatible_process', spawn):
            connect = asyncio.create_task(self.manager.connect('local'))
            await asyncio.wait_for(entered.wait(), 3)
            close = asyncio.create_task(self.manager.aclose())
            await asyncio.sleep(0)
            for _ in range(3):
                close.cancel()
                await asyncio.sleep(0)
            outcomes = await asyncio.wait_for(asyncio.gather(connect, close, return_exceptions=True), 3)
        self.assertIsInstance(outcomes[0], asyncio.CancelledError)
        self.assertIsInstance(outcomes[1], asyncio.CancelledError)
        self.assertEqual(len(pids), 1)
        self.assertFalse(process_exists(pids[0]))
        self.assertEqual(self.manager.tools(), ())

    async def test_http_queued_cancel_does_not_interrupt_another_remote_call(self):
        # HTTP uses the same admission rules while retaining its actual SDK
        # task groups and remote side effects; no model provider is contacted.
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen([sys.executable, str(SERVER), '--http', '--port', str(port)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(process.wait, 5)
        self.addCleanup(process.terminate)
        async with asyncio.timeout(5):
            while True:
                try:
                    _, writer = await asyncio.open_connection('127.0.0.1', port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(.01)
        self.store.add('http', {'transport': 'http', 'url': f'http://127.0.0.1:{port}/mcp'})
        await self.manager.connect('http')
        connection = self.manager.connection_identity('http')
        started, release, completed = (self.root / name for name in ('http-start', 'http-release', 'http-end'))
        active = asyncio.create_task(self.manager.call_tool('http', 'hold', {
            'started': str(started), 'release': str(release), 'completed': str(completed)}))
        self.addAsyncCleanup(self.finish, active)
        await self.wait_until(started.exists)
        effect = self.root / 'http-must-not-run'
        queued = asyncio.create_task(self.manager.call_tool('http', 'touch', {'path': str(effect)}))
        await self.wait_until(lambda: connection.queue.qsize() == 1)
        queued.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued
        release.touch()
        self.assertTrue((await active)['ok'])
        self.assertTrue(completed.exists())
        self.assertEqual(self.manager.status('http')['status'], 'connected')
        self.assertTrue((await self.manager.call_tool('http', 'echo', {'payload': {}}))['ok'])
        self.assertFalse(effect.exists())
        await self.manager.disconnect('http')

    def native_tools(self):
        from nailong.mcp.tools import build_mcp_tools
        from nailong.tools.execution import ToolExecutionContext
        from nailong.tools.files import FileSession
        execution = ToolExecutionContext(self.root)
        execution.approval_handler = lambda *_: {'type': 'approve'}
        observations = []
        execution.observer = observations.append
        tools = {tool.name: tool for tool in build_mcp_tools(self.manager, execution, FileSession(self.root))}
        return execution, observations, tools

    async def test_native_tool_message_distinguishes_gate_start_from_mcp_admission(self):
        # The execution gate must keep its trusted started flag; a second field
        # carries local MCP admission through the actual ToolMessage/observer.
        active, release, _ = await self.hold()
        self.connection.definition['timeout_seconds'] = 1
        _, observations, tools = self.native_tools()
        effect = self.root / 'must-not-run'
        tool = tools['mcp__local__touch']
        message = await tool.ainvoke({'type': 'tool_call', 'id': 'queued',
                                     'name': tool.name, 'args': {'path': str(effect)}})
        release.touch()
        self.assertTrue((await active)['ok'])
        value = json.loads(message.content)
        self.assertEqual(value['error_code'], 'mcp_timeout')
        self.assertIs(value['started'], True)
        self.assertIs(value.get('mcp_admitted'), False)
        self.assertIs(observations[-1].get('mcp_admitted'), False)
        self.assertFalse(effect.exists())

    async def test_native_bounded_success_keeps_local_mcp_admission(self):
        # A large remote payload cannot erase or spoof the local admission flag.
        _, observations, tools = self.native_tools()
        value = json.loads(await tools['mcp__local__echo'].ainvoke({
            'payload': {'padding': 'x' * 20_000, 'mcp_admitted': False}}))
        self.assertTrue(value['ok'])
        self.assertTrue(value['result_truncated'])
        self.assertIs(value.get('mcp_admitted'), True)
        self.assertIs(observations[-1].get('mcp_admitted'), True)

    async def test_native_pre_handler_rejections_cannot_claim_mcp_admission(self):
        execution, observations, tools = self.native_tools()
        execution.approval_handler = lambda *_: {'type': 'reject'}
        tool = tools['mcp__local__echo']
        rejected = json.loads(await tool.ainvoke({'payload': {}}))
        self.assertEqual(rejected['error_code'], 'approval_rejected')
        self.assertIs(rejected['started'], False)
        self.assertIs(rejected.get('mcp_admitted'), False)
        execution.approval_handler = lambda *_: {'type': 'approve'}
        await self.manager.disconnect('local')
        await self.manager.connect('local')
        stale = json.loads(await tool.ainvoke({'payload': {}}))
        self.assertEqual(stale['error_code'], 'mcp_connection_changed')
        self.assertIs(stale['started'], True)
        self.assertIs(stale.get('mcp_admitted'), False)
        self.assertIs(observations[-1].get('mcp_admitted'), False)

    async def test_manager_admission_metadata_covers_rejection_queue_and_disconnect(self):
        # Only the native owner decides admission; queued failures remain false
        # while an interrupted active request is conservatively true.
        for server, tool in (('absent', 'echo'), ('local', 'absent')):
            value = await self.manager.call_tool(server, tool, {})
            self.assertIs(value.get('mcp_admitted'), False)
        for tool, arguments in (('echo', {'payload': {}}), ('fail', {})):
            value = await self.manager.call_tool('local', tool, arguments)
            self.assertIs(value.get('mcp_admitted'), True)
        active, _, _ = await self.hold()
        waiting = [asyncio.create_task(self.manager.call_tool('local', 'echo', {'payload': {}}))
                   for _ in range(16)]
        for task in waiting:
            self.addAsyncCleanup(self.finish, task)
        await self.wait_until(lambda: self.connection.queue.qsize() == 16)
        overflow = await self.manager.call_tool('local', 'echo', {'payload': {}})
        self.assertEqual(overflow['error_code'], 'mcp_queue_full')
        self.assertIs(overflow.get('mcp_admitted'), False)
        await self.manager.disconnect('local')
        interrupted = await active
        self.assertIs(interrupted.get('mcp_admitted'), True)
        for value in await asyncio.gather(*waiting):
            self.assertEqual(value['error_code'], 'mcp_disconnected')
            self.assertIs(value.get('mcp_admitted'), False)

    @unittest.skipUnless(os.name == 'posix', 'POSIX closed-pipe SDK failure injection')
    async def test_sdk_internal_cancellation_seals_admission_before_native_teardown(self):
        # A broken native stdin writer cancels the SDK owner through its own
        # AnyIO TaskGroup. This is not a manager-initiated close. Sealing only
        # in `except Exception` misses CancelledError and admits more requests.
        import anyio
        from mcp.client.stdio import stdio_client
        pid_file = self.root / 'broken.pid'
        script = '''import json, os, sys, time
from pathlib import Path
Path(sys.argv[1]).write_text(str(os.getpid()))
for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    if method == 'initialize':
        result = {'protocolVersion': message['params']['protocolVersion'],
                  'capabilities': {'tools': {}}, 'serverInfo': {'name': 'broken-pipe', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'echo', 'inputSchema': {'type': 'object', 'properties': {}}}]}
    else:
        continue
    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
    if method == 'tools/list':
        os.close(0)
        time.sleep(20)
'''
        self.store.add('broken', {'transport': 'stdio', 'command': sys.executable,
                                 'args': ['-c', script, str(pid_file)], 'timeout_seconds': 5})
        entered, release = asyncio.Event(), asyncio.Event()
        interruptions = []
        self.addCleanup(release.set)

        @asynccontextmanager
        async def gated(*args, **kwargs):
            async with stdio_client(*args, **kwargs) as streams:
                try:
                    yield streams
                finally:
                    # Isolate the SDK's level cancellation. Only an extra raw
                    # Task.cancel from the manager can interrupt this cleanup.
                    with anyio.CancelScope(shield=True):
                        entered.set()
                        while not release.is_set():
                            try:
                                await release.wait()
                            except asyncio.CancelledError:
                                interruptions.append('manager recancelled native cleanup')

        with patch('mcp.client.stdio.stdio_client', gated):
            await self.manager.connect('broken')
            active = asyncio.create_task(self.manager.call_tool('broken', 'echo', {}))
            await asyncio.wait_for(entered.wait(), 3)
            connection = self.manager._connections['broken']
            status_during_cleanup = self.manager.status('broken')['status']
            queued = asyncio.create_task(self.manager.call_tool('broken', 'echo', {}))
            await asyncio.sleep(0)
            queue_size_during_cleanup = connection.queue.qsize()
            disconnect = asyncio.create_task(self.manager.disconnect('broken'))
            try:
                for _ in range(3):
                    await asyncio.sleep(0)
                interruptions_during_cleanup = list(interruptions)
            finally:
                release.set()
                outcomes = await asyncio.wait_for(asyncio.gather(active, queued, disconnect), 5)
        self.assertNotEqual(status_during_cleanup, 'connected')
        self.assertEqual(queue_size_during_cleanup, 0)
        self.assertEqual(interruptions_during_cleanup, [])
        self.assertIs(outcomes[0]['mcp_admitted'], True)
        self.assertIs(outcomes[1]['mcp_admitted'], False)
        self.assertFalse(process_exists(int(pid_file.read_text())))


if __name__ == '__main__':
    unittest.main()
