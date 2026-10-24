"""Custom SDK backends keep their own transport and close behavior."""
import importlib
import asyncio
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openai import AsyncOpenAI, OpenAI
from nailong.core.runtime_resources import RuntimeResourceOwner

RESPONSE=(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n'
          b'Content-Type: text/plain\r\nConnection: close\r\n\r\nOK')
URL='http://offline.invalid/v1/probe'


class ModelHTTPBackendCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,family,*,asynchronous,mounted,warm_url=None,default_subclass=False):
        http=importlib.import_module(family)
        core=importlib.import_module('httpcore2' if family=='httpx2' else 'httpcore')
        transport=(http.AsyncHTTPTransport if asynchronous else http.HTTPTransport)()
        backend=(core.AsyncMockBackend if asynchronous else core.MockBackend)([RESPONSE])
        if default_subclass:
            parent=type(transport._pool._network_backend)
            replay=backend
            class CustomBackend(parent):
                def connect_tcp(self,*args,**kwargs):return replay.connect_tcp(*args,**kwargs)
            # A name/module check would still mistake this SDK extension for
            # the default. Its actual stream intentionally has no raw socket.
            CustomBackend.__name__=parent.__name__
            CustomBackend.__module__=parent.__module__
            backend=CustomBackend()
        transport._pool._network_backend=backend
        options={'trust_env':False}
        if mounted:options['mounts']={'http://offline.invalid':transport}
        else:options['transport']=transport
        client=(http.AsyncClient if asynchronous else http.Client)(**options)
        # Even a bad mount must never turn this fixture into a real DNS request.
        self.assertIs(client._transport_for_url(http.URL(URL)),transport)
        transports=[client._transport,*client._mounts.values()]
        originals=[(item._pool,item._pool._network_backend) for item in transports if item is not None]
        sdk=(AsyncOpenAI if asynchronous else OpenAI)(api_key='offline-fixture',base_url='http://offline.invalid/v1',
            http_client=client,max_retries=0)
        resources=RuntimeResourceOwner();resources._init_resource_owner()
        native_sockets=[]
        try:
            if warm_url is not None:
                if asynchronous:response=await sdk.get(warm_url,cast_to=str)
                else:response=sdk.get(warm_url,cast_to=str)
                self.assertEqual(response,'OK')
                native_sockets=[connection._connection._network_stream.get_extra_info('socket')
                                for connection in client._transport._pool.connections]
                self.assertTrue(native_sockets)
                self.assertTrue(all(sock.fileno()>=0 for sock in native_sockets))
            resources._own_model(SimpleNamespace(**{'root_async_client' if asynchronous else 'root_client':sdk}))
            if asynchronous:response=await sdk.get(URL,cast_to=str)
            else:response=sdk.get(URL,cast_to=str)
            self.assertEqual(response,'OK')
            for pool,original in originals:self.assertIs(pool._network_backend,original)
            if asynchronous:await resources.aclose()
            else:resources.close()
            await asyncio.sleep(0)
            self.assertTrue(all(sock.fileno()==-1 for sock in native_sockets))
            self.assertTrue(sdk.is_closed())
            self.assertEqual(resources._owned_async_clients,[])
            self.assertEqual(resources._owned_sync_clients,[])
        finally:
            if asynchronous:await sdk.close()
            else:sdk.close()

    async def test_custom_sync_backends_preserve_actual_sdk_request_and_close(self):
        for family in ('httpx','httpx2'):
            with self.subTest(family=family):await self.exercise(family,asynchronous=False,mounted=False)

    async def test_custom_async_backends_preserve_actual_sdk_request_and_close(self):
        for family in ('httpx','httpx2'):
            with self.subTest(family=family):await self.exercise(family,asynchronous=True,mounted=False)

    async def test_same_name_default_sync_subclasses_keep_custom_sdk_contract(self):
        for family in ('httpx','httpx2'):
            with self.subTest(family=family):
                await self.exercise(family,asynchronous=False,mounted=False,default_subclass=True)

    async def test_same_name_default_async_subclasses_keep_custom_sdk_contract(self):
        for family in ('httpx','httpx2'):
            with self.subTest(family=family):
                await self.exercise(family,asynchronous=True,mounted=False,default_subclass=True)

    async def test_explicit_anyio_backends_keep_sdk_contract_and_close_real_sockets(self):
        from fixtures.model_http_probe import Handler, Server
        for family in ('httpx','httpx2'):
            with self.subTest(family=family):
                http=importlib.import_module(family)
                core=importlib.import_module('httpcore2' if family=='httpx2' else 'httpcore')
                transport=http.AsyncHTTPTransport();backend=core.AnyIOBackend()
                transport._pool._network_backend=backend
                client=http.AsyncClient(transport=transport,trust_env=False)
                sdk=AsyncOpenAI(api_key='offline-fixture',http_client=client,max_retries=0)
                resources=RuntimeResourceOwner();resources._init_resource_owner()
                server=Server(('127.0.0.1',0),Handler);server.mode='normal'
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                sockets=[]
                try:
                    resources._own_model(SimpleNamespace(root_async_client=sdk))
                    url=f'http://127.0.0.1:{server.server_address[1]}/anyio'
                    self.assertIs(client._transport_for_url(http.URL(url)),transport)
                    self.assertEqual(await sdk.get(url,cast_to=str),'OK')
                    sockets=[connection._connection._network_stream.get_extra_info('socket')
                             for connection in transport._pool.connections]
                    self.assertTrue(sockets)
                    self.assertIs(transport._pool._network_backend,backend)
                    await resources.aclose();await asyncio.sleep(0)
                    self.assertTrue(all(sock.fileno()==-1 for sock in sockets))
                    self.assertTrue(sdk.is_closed());self.assertEqual(resources._owned_async_clients,[])
                finally:
                    try:
                        await sdk.close()
                    finally:
                        await asyncio.to_thread(server.shutdown)
                        server.server_close();thread.join(timeout=1)

    async def test_anyio_fixture_releases_server_when_final_sdk_close_raises(self):
        from fixtures.model_http_probe import Server
        original_close=AsyncOpenAI.close
        for family in ('httpx','httpx2'):
            with self.subTest(family=family):
                servers=[]
                class ObservedServer(Server):
                    def __init__(self,*args,**kwargs):
                        super().__init__(*args,**kwargs)
                        servers.append(self)
                    def serve_forever(self,*args,**kwargs):
                        self.worker=threading.current_thread()
                        return super().serve_forever(*args,**kwargs)
                async def close_with_failure(sdk):
                    # Preserve real request/socket ownership and the first
                    # native close. Fail only the fixture's redundant close.
                    if sdk.is_closed() and type(sdk._client).__module__.split('.')[0]==family:
                        raise RuntimeError('injected final SDK close failure')
                    await original_close(sdk)
                fixture=type(self)('test_explicit_anyio_backends_keep_sdk_contract_and_close_real_sockets')
                try:
                    with patch('fixtures.model_http_probe.Server',ObservedServer),patch.object(AsyncOpenAI,'close',new=close_with_failure):
                        with self.assertRaisesRegex(RuntimeError,'injected final SDK close failure'):
                            await fixture.test_explicit_anyio_backends_keep_sdk_contract_and_close_real_sockets()
                    self.assertTrue(servers)
                    for server in servers:
                        self.assertEqual(server.socket.fileno(),-1,'fixture must release its listening socket even when SDK close fails')
                        self.assertFalse(server.worker.is_alive(),'fixture must join its serving thread before propagating the close failure')
                finally:
                    # Rescue only this regression's owned fixture if the
                    # assertion detects the old bug; never mask that failure.
                    for server in servers:
                        if server.worker.is_alive():await asyncio.to_thread(server.shutdown)
                        server.server_close();server.worker.join(timeout=1)

    async def test_mixed_sync_mounts_do_not_partially_decorate_default_pool(self):
        for family in ('httpx','httpx2'):
            with self.subTest(family=family):await self.exercise(family,asynchronous=False,mounted=True)

    async def test_mixed_async_mounts_do_not_partially_decorate_default_pool(self):
        for family in ('httpx','httpx2'):
            with self.subTest(family=family):await self.exercise(family,asynchronous=True,mounted=True)

    async def warm_mixed(self,family,*,asynchronous):
        from fixtures.model_http_probe import Handler, Server
        server=Server(('127.0.0.1',0),Handler);server.mode='normal'
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            await self.exercise(family,asynchronous=asynchronous,mounted=True,
                warm_url=f'http://127.0.0.1:{server.server_address[1]}/warm')
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close();thread.join(timeout=1)

    async def test_used_default_pool_with_custom_sync_mount_keeps_original_contract(self):
        for family in ('httpx','httpx2'):
            with self.subTest(family=family):await self.warm_mixed(family,asynchronous=False)

    async def test_used_default_pool_with_custom_async_mount_keeps_original_contract(self):
        for family in ('httpx','httpx2'):
            with self.subTest(family=family):await self.warm_mixed(family,asynchronous=True)

    async def used_default(self,family,*,asynchronous):
        from fixtures.model_http_probe import Handler, Server
        http=importlib.import_module(family)
        client=(http.AsyncClient if asynchronous else http.Client)(trust_env=False)
        sdk=(AsyncOpenAI if asynchronous else OpenAI)(api_key='offline-fixture',http_client=client,max_retries=0)
        server=Server(('127.0.0.1',0),Handler);server.mode='normal'
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        sockets=[]
        try:
            url=f'http://127.0.0.1:{server.server_address[1]}/warm'
            if asynchronous:response=await sdk.get(url,cast_to=str)
            else:response=sdk.get(url,cast_to=str)
            self.assertEqual(response,'OK')
            pool=client._transport._pool;backend=pool._network_backend
            sockets=[connection._connection._network_stream.get_extra_info('socket') for connection in pool.connections]
            self.assertTrue(sockets)
            resources=RuntimeResourceOwner();resources._init_resource_owner()
            with self.assertRaisesRegex(RuntimeError,'already-used'):
                resources._own_model(SimpleNamespace(**{'root_async_client' if asynchronous else 'root_client':sdk}))
            self.assertIs(pool._network_backend,backend)
            self.assertFalse(sdk.is_closed(),'rejected clients still belong to their original caller')
        finally:
            try:
                if asynchronous:await sdk.close()
                else:sdk.close()
                await asyncio.sleep(0)
                self.assertTrue(all(sock.fileno()==-1 for sock in sockets))
            finally:
                await asyncio.to_thread(server.shutdown)
                server.server_close();thread.join(timeout=1)

    async def test_used_default_sync_client_is_still_rejected_before_decoration(self):
        for family in ('httpx','httpx2'):
            with self.subTest(family=family):await self.used_default(family,asynchronous=False)

    async def test_used_default_async_client_is_still_rejected_before_decoration(self):
        for family in ('httpx','httpx2'):
            with self.subTest(family=family):await self.used_default(family,asynchronous=True)
