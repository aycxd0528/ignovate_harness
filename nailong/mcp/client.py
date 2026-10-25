"""MCP connections whose SDK contexts are entered and closed by one owner task."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta

from nailong.mcp.config import MCPConfigStore, resolve_references
from nailong.mcp.naming import tool_name
from nailong.tools.results import failure, redact


@dataclass
class _Request:
    tool: str
    arguments: dict
    result: asyncio.Future
    caller: asyncio.Task
    cancel_count: int
    deadline: float
    started: bool = False
    finished: bool = False


@dataclass
class _Connection:
    name: str
    definition: dict
    status: str = 'connecting'
    error: str = ''
    descriptors: tuple = ()
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=16))
    task: asyncio.Task | None = None
    ready: asyncio.Future | None = None
    closing: bool = False


@asynccontextmanager
async def _owner_stack(connection):
    async with AsyncExitStack() as stack:
        try:
            yield stack
        finally:
            # SDK task groups may cancel this owner themselves (e.g. broken
            # transport). Seal admission before any SDK __aexit__, including
            # failures/cancellation that did not originate in _begin_close.
            connection.closing = True
            connection.descriptors = ()
            connection.status = 'error' if connection.error else 'disconnecting'


async def _join(task):
    """Finish the owner's teardown even when the requesting UI task is cancelled."""
    # gather turns the owner's expected cancellation into a value, so only a
    # cancellation of this waiter is caught here. Shield prevents recancelling
    # SDK task groups while their original owner unwinds them.
    completion = asyncio.gather(task, return_exceptions=True)
    cancelled = None
    while not completion.done():
        try:
            await asyncio.shield(completion)
        except asyncio.CancelledError as error:
            cancelled = error
    result = completion.result()[0]
    error = result if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError) else None
    if cancelled is not None:
        raise cancelled from error
    if error is not None:
        raise error


class MCPManager:
    def __init__(self, store: MCPConfigStore, *, api_key=''):
        self.store = store
        self._connections: dict[str, _Connection] = {}
        self._secrets = {api_key} if api_key else set()
        self._api_key = api_key
        self._lifecycle_lock = asyncio.Lock()
        self._shutdown_task = None

    def sanitize(self, value):
        if isinstance(value, dict):
            return {self.sanitize(str(key)): self.sanitize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.sanitize(item) for item in value]
        for secret in sorted(self._secrets, key=len, reverse=True):
            value = redact(value, secret)
        return value

    def status(self, name: str) -> dict:
        connection = self._connections.get(name)
        if connection is None:
            return {'status': 'disconnected', 'tool_count': 0, 'error': ''}
        connected = connection.status == 'connected' and not connection.task.done()
        return {'status': connection.status if not connection.task.done() else ('error' if connection.error else 'disconnected'),
                'tool_count': len(connection.descriptors) if connected else 0, 'error': connection.error}

    def tools(self, name: str | None = None) -> tuple:
        if name is not None:
            connection = self._connections.get(name)
            return connection.descriptors if connection and self.status(name)['status'] == 'connected' else ()
        return tuple((server, descriptor) for server in self._connections for descriptor in self.tools(server))

    def connection_identity(self, name: str):
        return self._connections.get(name) if self.status(name)['status'] == 'connected' else None

    @property
    def has_live_connections(self):
        """Whether an async owner still needs this manager's shutdown loop."""
        return any(not connection.task.done() for connection in self._connections.values())

    async def connect(self, name: str) -> dict:
        if self._shutting_down:
            raise ValueError('MCP 服务正在关闭；请等待清理完成后重新连接。')
        async with self._lifecycle_lock:
            if self._shutting_down:
                raise ValueError('MCP 服务正在关闭；请等待清理完成后重新连接。')
            definition = self.store.list_servers().get(name)
            if definition is None:
                raise ValueError('MCP 服务未配置；先使用 /mcp add。')
            if self.status(name)['status'] == 'connected':
                return self.status(name)
            await self._disconnect(name)
            if self._shutting_down:
                raise ValueError('MCP 服务正在关闭；请等待清理完成后重新连接。')
            connection = _Connection(name, definition)
            self._connections[name] = connection
            ready = asyncio.get_running_loop().create_future()
            connection.ready = ready
            connection.task = asyncio.create_task(self._serve(connection, ready), name=f'mcp:{name}')
            try:
                await asyncio.wait_for(asyncio.shield(ready), definition.get('timeout_seconds', 30))
            except asyncio.CancelledError:
                try:
                    await self._disconnect(name)
                finally:
                    self._discard_ready(ready)
                raise
            except Exception as error:
                try:
                    await self._disconnect(name)
                finally:
                    self._discard_ready(ready)
                connection.status = 'error'
                connection.error = connection.error or f'MCP 连接失败（{type(error).__name__}）；请检查服务配置、凭据与可执行文件。'
                raise ValueError(connection.error) from None
            return self.status(name)

    @staticmethod
    def _discard_ready(ready):
        if ready.done() and not ready.cancelled():
            ready.exception()
        else:
            ready.cancel()

    async def _serve(self, connection, ready):
        active_request = None
        try:
            # Lazy imports keep diagnostics and offline configuration usable without SDK.
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from mcp.client.streamable_http import streamable_http_client
            import httpx
            definition = connection.definition
            timeout = definition.get('timeout_seconds', 30)
            values = resolve_references(definition.get('env' if definition['transport'] == 'stdio' else 'headers', {}))
            if self._api_key and any(self._api_key in value for value in values.values()):
                raise ValueError('MCP 不能使用模型 API 密钥。')
            self._secrets.update(values.values())
            async with _owner_stack(connection) as stack:
                if definition['transport'] == 'stdio':
                    # SDK inherits only its own minimal platform allowlist, plus explicit env.
                    errlog = stack.enter_context(open(os.devnull, 'w'))
                    command,args=definition['command'],definition.get('args',[])
                    ownership=None
                    if os.name=='posix':
                        from nailong.mcp.stdio_owner import StdioOwnership
                        ownership=await stack.enter_async_context(StdioOwnership(connection))
                        helper=Path(__file__).resolve().parents[1]/'core'/'_mcp_stdio_owner.py'
                        command,args=sys.executable,['-I','-S',str(helper),str(os.getpid()),
                            *ownership.arguments,command,*args]
                    parameters = StdioServerParameters(command=command,
                        args=args, env=values, cwd=str(self.store.project_root))
                    streams = await stack.enter_async_context(stdio_client(parameters, errlog=errlog))
                    if ownership is not None:await ownership.attach()
                else:
                    http = await stack.enter_async_context(httpx.AsyncClient(headers=values,
                        timeout=httpx.Timeout(timeout), follow_redirects=False))
                    streams = await stack.enter_async_context(streamable_http_client(definition['url'], http_client=http))
                session = await stack.enter_async_context(ClientSession(streams[0], streams[1],
                    read_timeout_seconds=timedelta(seconds=timeout)))
                await session.initialize()
                descriptors = []
                cursor = None
                seen = set()
                for _ in range(32):
                    page = await session.list_tools(cursor=cursor)
                    descriptors.extend(page.tools)
                    if len(descriptors) > 256:
                        raise ValueError('MCP 工具数超过 256。')
                    cursor = page.nextCursor
                    if not cursor:
                        break
                    if cursor in seen:
                        raise ValueError('MCP 工具分页游标重复。')
                    seen.add(cursor)
                else:
                    raise ValueError('MCP 工具分页超过 32 页。')
                names = [item.name for item in descriptors]
                if len(set(names)) != len(names):
                    raise ValueError('MCP 服务返回重复工具名称。')
                registered = {tool_name(server, tool.name) for server, tool in self.tools()}
                for item in descriptors:
                    if not item.name or len(item.name) > 256:
                        raise ValueError('MCP 工具名称为空或过长。')
                    public_name = tool_name(connection.name, item.name)
                    if self.sanitize(item.name) != item.name or self.sanitize(public_name) != public_name:
                        raise ValueError('MCP 工具名称包含凭据，已拒绝注册。')
                    if public_name in registered:
                        raise ValueError('MCP 工具注册名称发生冲突。')
                    registered.add(public_name)
                    from nailong.mcp.schema import schema_validator, check_provider_schema
                    schema_validator(item.inputSchema)
                    item.description = self.sanitize(item.description or '')
                    item.inputSchema = self.sanitize(item.inputSchema)
                    check_provider_schema(public_name, item.inputSchema, item.description)
                connection.descriptors = tuple(descriptors)
                connection.status = 'connected'
                if not ready.done():
                    ready.set_result(None)
                while not connection.closing:
                    request = await connection.queue.get()
                    connection.queue.task_done()
                    if request.result.done():
                        continue
                    if request.caller.cancelling() > request.cancel_count:
                        request.result.cancel()
                        continue
                    if asyncio.get_running_loop().time() >= request.deadline:
                        request.result.set_result(failure('mcp_timeout',
                            'MCP 排队等待超时；该请求未执行，连接保持。',
                            timed_out=True, started=False, mcp_admitted=False))
                        request.finished = True
                        continue
                    # No await between observing withdrawal and admitting this
                    # request. Only the SDK owner can mark it as started.
                    active_request = request
                    request.started = True
                    try:
                        response = await session.call_tool(request.tool, arguments=request.arguments)
                        value = self._result(response)
                    except Exception as error:
                        value = failure('mcp_transport_error', f'MCP 调用失败（{type(error).__name__}）；未自动重试，副作用需核对。',
                                        started=True, mcp_admitted=True)
                        connection.error = value['error']
                        # This owner is already unwinding its SDK contexts;
                        # joining it must not issue another cancellation.
                        connection.closing = True
                        connection.status = 'error'
                        connection.descriptors = ()
                    if not request.result.done():
                        request.result.set_result(self.sanitize(value))
                    request.finished = True
                    active_request = None
                    if connection.error:
                        break
        except asyncio.CancelledError:
            raise
        except Exception as error:
            connection.error = connection.error or f'MCP 连接失败（{type(error).__name__}）；请检查服务配置、凭据与可执行文件。'
            if not ready.done():
                ready.set_exception(ValueError(connection.error))
        finally:
            connection.descriptors = ()
            connection.status = 'error' if connection.error else 'disconnected'
            if not ready.done():
                ready.cancel()
            if active_request is not None and not active_request.result.done():
                active_request.result.set_result(failure('mcp_disconnected',
                    'MCP 连接已关闭；进行中的操作可能产生副作用，需核对结果。', started=True, mcp_admitted=True))
            while not connection.queue.empty():
                request = connection.queue.get_nowait()
                connection.queue.task_done()
                if not request.result.done():
                    request.result.set_result(failure('mcp_disconnected', 'MCP 连接已关闭；未执行排队请求。',
                                                      started=False, mcp_admitted=False))

    @staticmethod
    def _result(response) -> dict:
        text = []
        omitted = []
        for content in response.content:
            if content.type == 'text':
                text.append(content.text)
            elif content.type == 'resource' and hasattr(content.resource, 'text'):
                text.append(content.resource.text)
            else:
                omitted.append(content.type)
        value = {'ok': not response.isError, 'content': '\n'.join(text), 'mcp_admitted': True}
        if response.structuredContent is not None:
            value['structured_content'] = response.structuredContent
        if omitted:
            value.update(omitted_content_types=omitted, truncated=True,
                         hint='非文本内容已省略；当前终端仅展示文本和结构化结果。')
        if response.isError:
            value.update(error_code='mcp_tool_error', error=value['content'] or 'MCP 服务报告工具失败。')
        return value

    async def call_tool(self, server: str, tool: str, arguments: dict) -> dict:
        connection = self._connections.get(server)
        if connection is None or self.status(server)['status'] != 'connected':
            return failure('mcp_disconnected', 'MCP 服务未连接；请使用 /mcp connect。', started=False, mcp_admitted=False)
        if tool not in {item.name for item in connection.descriptors}:
            return failure('mcp_unknown_tool', 'MCP 工具未在当前连接中发现。', started=False, mcp_admitted=False)
        loop = asyncio.get_running_loop()
        caller = asyncio.current_task()
        timeout = connection.definition.get('timeout_seconds', 30)
        request = _Request(tool, arguments, loop.create_future(), caller, caller.cancelling(), loop.time() + timeout)
        try:
            connection.queue.put_nowait(request)
        except asyncio.QueueFull:
            return failure('mcp_queue_full', 'MCP 服务的待执行请求已达上限。', started=False, mcp_admitted=False)
        try:
            value = await asyncio.wait_for(request.result, timeout)
            if connection.error:
                await self._close(connection)
            return value
        except asyncio.CancelledError:
            if not request.started:
                self._withdraw(connection)
            elif not request.finished:
                await self._close(connection)
            raise
        except TimeoutError:
            if not request.started:
                self._withdraw(connection)
                return failure('mcp_timeout', 'MCP 排队等待超时；该请求未执行，连接保持。',
                               timed_out=True, started=False, mcp_admitted=False)
            if not request.finished or connection.error:
                await self._close(connection)
            return failure('mcp_timeout', 'MCP 调用超时；未自动重试，副作用需核对。',
                           timed_out=True, started=True, mcp_admitted=True)

    @staticmethod
    def _withdraw(connection):
        # At most 16 entries; draining/restoring without await also releases
        # capacity immediately while the SDK owner is busy with another call.
        pending = []
        while not connection.queue.empty():
            request = connection.queue.get_nowait()
            connection.queue.task_done()
            if not request.result.done():
                pending.append(request)
        for request in pending:
            connection.queue.put_nowait(request)

    async def _disconnect(self, name):
        connection = self._connections.get(name)
        await self._close(connection)

    async def _close(self, connection):
        if connection and connection.task:
            self._begin_close(connection)
            await _join(connection.task)

    @staticmethod
    def _begin_close(connection):
        # Also wake connect when an SDK failure already sealed admission but
        # is still unwinding before it can publish ready's original error.
        if connection.ready is not None and not connection.ready.done():
            connection.ready.cancel()
        if connection.closing:
            return
        connection.closing = True
        connection.status = 'disconnecting'
        connection.descriptors = ()
        if connection.task is not None and not connection.task.done():
            connection.task.cancel()

    async def disconnect(self, name: str) -> None:
        # Capture this generation before the first await. Closing its SDK owner
        # needs no lifecycle lock: connect already joins that same owner before
        # replacement. An unrelated connecting server cannot defer admission of
        # this stop or let a cancelled waiter abandon its resources.
        await self._close(self._connections.get(name))

    async def aclose(self) -> None:
        # Admit shutdown before waiting for the lifecycle lock. All resources
        # remain owned even if this controller is cancelled while connect or
        # disconnect holds the lock. Concurrent callers join the same shutdown.
        if not self._shutting_down:
            connections = tuple(self._connections.values())
            for connection in connections:
                self._begin_close(connection)
            self._shutdown_task = asyncio.create_task(self._shutdown(connections), name='mcp:shutdown')
        await _join(self._shutdown_task)

    @property
    def _shutting_down(self):
        return self._shutdown_task is not None and not self._shutdown_task.done()

    async def _shutdown(self, connections):
        async with self._lifecycle_lock:
            for connection in connections:
                await self._close(connection)
