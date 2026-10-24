"""A closing connection must still consume its native guardian's final receipt."""
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from nailong.mcp.client import MCPManager
from nailong.mcp.config import MCPConfigStore
from nailong.mcp.stdio_owner import StdioOwnership


@unittest.skipUnless(os.name=='posix','native POSIX guardian receipt')
class MCPClosingReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,*,destroy_owner,watcher_cancelled=False):
        import mcp.client.stdio as stdio
        native_stdio=stdio.stdio_client;native_watch=StdioOwnership._watch
        stdio_finished=asyncio.Event();channels=[];native_receipts=[]

        async def late_watch(channel):
            channels.append(channel)
            await stdio_finished.wait()
            return await native_watch(channel)

        @asynccontextmanager
        async def observed_stdio(*args,**kwargs):
            try:
                async with native_stdio(*args,**kwargs) as streams:yield streams
            finally:
                # The real SDK has waited for its actual direct process to
                # exit. Inspect, but do not consume, its buffered final bytes.
                try:native_receipts.append(channels[0].peer.recv(2,socket.MSG_PEEK))
                finally:stdio_finished.set()

        with tempfile.TemporaryDirectory(prefix='MCP closing receipt 原生 ') as temporary:
            root=Path(temporary).resolve();store=MCPConfigStore(root)
            store.add('local',{'transport':'stdio','command':sys.executable,
                'args':['-B',str(Path(__file__).parent/'fixtures/mcp_owned_service.py')],
                'timeout_seconds':5})
            manager=MCPManager(store)
            with patch.object(stdio,'stdio_client',new=observed_stdio),patch.object(StdioOwnership,'_watch',new=late_watch):
                call=None
                try:
                    await manager.connect('local')
                    self.assertEqual(manager.status('local')['status'],'connected')
                    if watcher_cancelled:
                        channels[0].watcher.cancel()
                        await asyncio.gather(channels[0].watcher,return_exceptions=True)
                    if destroy_owner:
                        marker=root/'inner-owner-killed'
                        call=asyncio.create_task(manager.call_tool('local','destroy_owner',{'marker':str(marker)}))
                        async with asyncio.timeout(3):
                            while not marker.exists():await asyncio.sleep(.005)
                    await manager.disconnect('local')
                    if call is not None:await call
                    self.assertTrue(stdio_finished.is_set())
                    self.assertEqual(native_receipts,[b'' if destroy_owner else b'C'])
                    self.assertEqual(manager.tools('local'),())
                    result=await manager.call_tool('local','echo',{'payload':{'closed':'must not execute'}})
                    self.assertFalse(result['ok']);self.assertFalse(result.get('mcp_admitted',False))
                    status=manager.status('local')
                    if destroy_owner or watcher_cancelled:
                        self.assertIn('清理无法核实',status['error'])
                        self.assertEqual(status['status'],'error')
                    else:
                        self.assertEqual(status,{'status':'disconnected','tool_count':0,'error':''})
                    self.assertFalse(manager.has_live_connections)
                finally:
                    stdio_finished.set()
                    await manager.aclose()
                    if call is not None:await asyncio.gather(call,return_exceptions=True)

    async def test_late_unknown_receipt_after_native_sdk_exit_is_not_reported_as_clean_disconnect(self):
        await self.exercise(destroy_owner=True)

    async def test_late_verified_receipt_keeps_explicit_disconnect_clean(self):
        await self.exercise(destroy_owner=False)

    async def test_cancelled_observer_does_not_claim_verified_cleanup_from_sdk_exit(self):
        await self.exercise(destroy_owner=False,watcher_cancelled=True)
