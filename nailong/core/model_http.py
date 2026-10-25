"""Retain default model HTTP streams independently of SDK pool bookkeeping."""
import asyncio
import threading
from importlib import import_module


class _NativeHTTPCloseTask(asyncio.Task):
    def cancel(self,msg=None):
        # Only final native-stream settlement runs here. SDK requests, deadlines,
        # connect/read/write and SDK.close itself run in their original tasks.
        return False


def _raise_errors(errors):
    if len(errors)==1:raise errors[0]
    if errors:raise BaseExceptionGroup('Model HTTP cleanup failed',errors)


class _Stream:
    def __init__(self,owner,native):
        self._owner=owner;self._native=native
        self._socket=native.get_extra_info('socket')
        self._sync_lock=threading.RLock();self._async_lock=asyncio.Lock()
        owner.add(self)

    def get_extra_info(self,info):
        return self._native.get_extra_info(info)

    def _verify_closed(self):
        if self._socket is None or self._socket.fileno()!=-1:
            raise RuntimeError('Model HTTP native socket closure could not be verified.')
        self._owner.remove(self)

    def read(self,*args,**kwargs):return self._native.read(*args,**kwargs)
    def write(self,*args,**kwargs):return self._native.write(*args,**kwargs)

    def close(self):
        with self._sync_lock:
            try:
                self._native.close()
                self._verify_closed()
            except BaseException:
                self._owner._seal()
                raise

    def start_tls(self,*args,**kwargs):
        self._owner.begin()
        try:
            with self._sync_lock:
                try:
                    self._native=self._native.start_tls(*args,**kwargs)
                    self._socket=self._native.get_extra_info('socket')
                    return self
                except BaseException as error:
                    try:self.close()
                    except BaseException as cleanup:raise BaseExceptionGroup('Model HTTP TLS and cleanup failed',[error,cleanup])
                    raise
        finally:self._owner.end()


class _AsyncStream(_Stream):
    async def read(self,*args,**kwargs):return await self._native.read(*args,**kwargs)
    async def write(self,*args,**kwargs):return await self._native.write(*args,**kwargs)

    async def aclose(self):
        async with self._async_lock:
            try:
                await self._native.aclose()
                # asyncio transports close their socket from an already queued
                # connection_lost callback. Inspect its descriptor afterward.
                await asyncio.sleep(0)
                self._verify_closed()
            except asyncio.CancelledError:
                # A normal cancelled request may still reuse its model later;
                # retain this stream for settlement without sealing that model.
                raise
            except BaseException:
                self._owner._seal()
                raise

    async def start_tls(self,*args,**kwargs):
        self._owner.begin()
        try:
            try:
                async with self._async_lock:
                    self._native=await self._native.start_tls(*args,**kwargs)
                    self._socket=self._native.get_extra_info('socket')
                    return self
            except BaseException as error:
                try:await self.aclose()
                except BaseException as cleanup:raise BaseExceptionGroup('Model HTTP TLS and cleanup failed',[error,cleanup])
                raise
        finally:self._owner.end()


class _Backend:
    def __init__(self,owner,native):self._owner=owner;self._native=native
    def __getattr__(self,name):return getattr(self._native,name)

    def _connect(self,method,*args,**kwargs):
        self._owner.begin()
        try:return _Stream(self._owner,getattr(self._native,method)(*args,**kwargs))
        finally:self._owner.end()

    def connect_tcp(self,*args,**kwargs):return self._connect('connect_tcp',*args,**kwargs)
    def connect_unix_socket(self,*args,**kwargs):return self._connect('connect_unix_socket',*args,**kwargs)


class _AsyncBackend(_Backend):
    async def _connect(self,method,*args,**kwargs):
        self._owner.begin()
        try:return _AsyncStream(self._owner,await getattr(self._native,method)(*args,**kwargs))
        finally:self._owner.end()

    async def connect_tcp(self,*args,**kwargs):return await self._connect('connect_tcp',*args,**kwargs)
    async def connect_unix_socket(self,*args,**kwargs):return await self._connect('connect_unix_socket',*args,**kwargs)


class ModelHTTPOwner:
    def __init__(self,client,*,asynchronous):
        self.client=client;self.asynchronous=asynchronous
        self._condition=threading.Condition();self._streams={}
        self._connecting=0;self._closing=False;self._started=False

    @property
    def started(self):
        with self._condition:return self._started

    def begin(self):
        with self._condition:
            if self._closing:raise RuntimeError('Model HTTP resources are closing or closed.')
            self._started=True
            self._connecting+=1

    def end(self):
        with self._condition:
            self._connecting-=1;self._condition.notify_all()

    def add(self,stream):
        with self._condition:self._streams[id(stream)]=stream

    def remove(self,stream):
        with self._condition:self._streams.pop(id(stream),None)

    def _seal(self):
        with self._condition:self._closing=True

    def _snapshot(self):
        with self._condition:return list(self._streams.values())

    def close(self):
        self._seal();errors=[]
        try:self.client.close()
        except BaseException as error:errors.append(error)
        with self._condition:
            while self._connecting:self._condition.wait()
        for stream in self._snapshot():
            try:stream.close()
            except BaseException as error:errors.append(error)
        _raise_errors(errors)

    async def _settle(self):
        while True:
            with self._condition:connecting=self._connecting
            if not connecting:break
            await asyncio.sleep(.001)
        errors=[]
        for stream in self._snapshot():
            try:await stream.aclose()
            except BaseException as error:errors.append(error)
        _raise_errors(errors)

    async def aclose(self):
        from nailong.core.runtime_resources import await_owned
        self._seal();errors=[]
        try:await self.client.close()
        except BaseException as error:errors.append(error)
        cleanup=_NativeHTTPCloseTask(self._settle(),loop=asyncio.get_running_loop(),name='runtime:http-native-close')
        try:await await_owned(cleanup)
        except BaseException as error:errors.append(error)
        _raise_errors(errors)


def _default_backend(backend,*,asynchronous):
    family=type(backend).__module__.split('.')[0]
    if family not in ('httpcore','httpcore2'):return False
    module,name=('_backends.auto','AutoBackend') if asynchronous else ('_backends.sync','SyncBackend')
    try:expected=getattr(import_module(f'{family}.{module}'),name)
    except (ImportError,AttributeError):return False
    return type(backend) is expected


def own_model_http(client,*,asynchronous):
    """Decorate only this default HTTPX/HTTPX2 instance's native pool backend."""
    http=getattr(client,'_client',None)
    if http is None:return None
    transports=[getattr(http,'_transport',None),*getattr(http,'_mounts',{}).values()]
    pools=[]
    for transport in transports:
        if transport is None:continue
        if type(transport).__module__.split('.')[0] not in ('httpx','httpx2'):return None
        pool=getattr(transport,'_pool',None)
        if pool is None or not hasattr(pool,'_network_backend'):return None
        if not any(existing is pool for existing in pools):pools.append(pool)
    if not pools:return None
    for pool in pools:
        backend=pool._network_backend
        if isinstance(backend,_Backend):return backend._owner
        if not _default_backend(backend,asynchronous=asynchronous):return None
    for pool in pools:
        # Product construction owns fresh clients before any request. An external
        # already-used client cannot be retroactively given acquisition receipts.
        if pool.connections:raise RuntimeError('Cannot own an already-used model HTTP client.')
    owner=ModelHTTPOwner(client,asynchronous=asynchronous)
    backend_type=_AsyncBackend if asynchronous else _Backend
    for pool in pools:pool._network_backend=backend_type(owner,pool._network_backend)
    return owner
