"""Resource ownership shared by the synchronous and asynchronous runtime factory."""
import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor

from nailong.core.sqlite_lifecycle import start_sqlite_lifecycle


def raise_cleanup_errors(errors):
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup('Runtime resource cleanup failed', errors)


async def await_owned(task):
    """Join an owner to completion before propagating a waiter's cancellation."""
    if task.done():
        # Completed cleanup can be observed again after its loop has closed.
        # gather would attach a callback to that closed loop even for done tasks.
        return task.result()
    completion = asyncio.gather(task, return_exceptions=True)
    cancelled = None
    while not completion.done():
        try:
            await asyncio.shield(completion)
        except asyncio.CancelledError as error:
            if cancelled is None:
                cancelled = error
    result = completion.result()[0]
    failed = isinstance(result, BaseException)
    if cancelled is not None:
        raise cancelled from result if failed else None
    if failed:
        raise result
    return result


async def close_runtime_factories(factories):
    """Attempt every owned factory, including when a previous close fails."""
    async def close_all():
        errors = []
        for factory in reversed(list(factories)):
            aclose = getattr(factory, 'aclose', None)
            if callable(aclose):
                try:
                    await aclose()
                except BaseException as error:
                    errors.append(error)
            close = getattr(factory, 'close', None)
            if callable(close):
                try:
                    close()
                except BaseException as error:
                    errors.append(error)
        raise_cleanup_errors(errors)

    await await_owned(asyncio.create_task(close_all(), name='runtime:close-factories'))


class RuntimeResourceOwner:
    def _init_resource_owner(self):
        self._resources_closing = False
        self._resource_close_task = None
        self._async_resource_lock = asyncio.Lock()
        self._checkpointer_context = None
        self._async_checkpointer_context = None
        self._unpublished_savers = []
        self.async_checkpointer = None
        self._session_token_budgets = {}
        self._owned_sync_clients = []
        self._owned_async_clients = []
        self._model_http_owners = {}
        self._model_client_closes = {}
        self._async_resources_started = False

    def _ensure_open(self):
        if self._resources_closing:
            raise RuntimeError('Runtime factory is closing or closed. 请创建新的运行时。')

    def _own_model(self, model):
        for name, clients in (('root_client', self._owned_sync_clients),
                              ('root_async_client', self._owned_async_clients)):
            client = getattr(model, name, None)
            if client is not None and callable(getattr(client, 'close', None)):
                if not any(existing is client for existing in clients):
                    from nailong.core.model_http import own_model_http
                    owner=own_model_http(client,asynchronous=name=='root_async_client')
                    if owner is not None:self._model_http_owners[id(client)]=owner
                    clients.append(client)
        return model

    async def _close_unpublished_saver(self, record):
        await self._exit_saver(record['context'], record['saver'])
        self._unpublished_savers.remove(record)

    @staticmethod
    async def _exit_saver(context, saver):
        errors = []
        try:
            await context.__aexit__(None, None, None)
        except BaseException as error:
            errors.append(error)
        # asynccontextmanager generators are exhausted even if their finally
        # raises. A second __aexit__ can therefore report success without ever
        # retrying conn.close. Retain and close the native handle explicitly.
        if saver is not None:
            try:
                await saver.conn.close()
            except BaseException as error:
                errors.append(error)
        raise_cleanup_errors(errors)

    async def _enter_saver(self, record):
        context = record['context']
        try:
            saver = await context.__aenter__()
            record['saver'] = saver
            await saver.setup()
        except BaseException as error:
            try:
                await self._close_unpublished_saver(record)
            except BaseException as cleanup_error:
                raise BaseExceptionGroup('SQLite initialization and cleanup failed', [error, cleanup_error])
            raise
        return saver

    async def _get_async_saver(self, context_factory):
        self._ensure_open()
        self._async_resources_started = True
        async with self._async_resource_lock:
            self._ensure_open()
            if self.async_checkpointer is not None:
                return self.async_checkpointer
            # Failed rollback must be settled before allocating a replacement.
            # Otherwise repeated setup failures can accumulate native handles.
            for record in list(self._unpublished_savers):
                await await_owned(start_sqlite_lifecycle(
                    self._close_unpublished_saver(record),name='runtime:sqlite-rollback'))
            context = context_factory()
            record = {'context': context, 'saver': None}
            self._unpublished_savers.append(record)
            opening = start_sqlite_lifecycle(self._enter_saver(record), name='runtime:sqlite-open')
            try:
                saver = await asyncio.shield(opening)
            except asyncio.CancelledError as cancelled:
                async def discard():
                    # Opening/setup owns its rollback on failure. On success this
                    # caller still owns the unpublished context and must close it.
                    await opening
                    await self._close_unpublished_saver(record)

                try:
                    await await_owned(start_sqlite_lifecycle(discard(), name='runtime:sqlite-discard'))
                except BaseException as cleanup_error:
                    raise cancelled from cleanup_error
                raise
            if self._resources_closing:
                await await_owned(start_sqlite_lifecycle(
                    self._close_unpublished_saver(record),name='runtime:sqlite-rollback'))
                self._ensure_open()
            self._async_checkpointer_context = context
            self.async_checkpointer = saver
            self._unpublished_savers.remove(record)
            return saver

    async def _close_published_saver(self,context,saver):
        async with saver.lock:
            await self._exit_saver(context,saver)
        # Publish the verified resource state before a cancelled close waiter
        # resumes. A failed exit retains both context and native handle.
        self._async_checkpointer_context = None
        self.async_checkpointer = None

    def _forget_model_client(self,client,clients):
        # Preserve list aliases and compare identities, not SDK equality.
        clients[:]=[existing for existing in clients if existing is not client]
        self._model_http_owners.pop(id(client),None)

    def _close_sync_model_client(self,client):
        owner=self._model_http_owners.get(id(client))
        (owner.close if owner is not None else client.close)()
        self._forget_model_client(client,self._owned_sync_clients)

    async def _aclose_sync_model_client(self,client):
        # A sync TLS upgrade may still be handing a socket to its HTTP backend.
        # Join it off-loop. The executor Future is not an asyncio Task targeted
        # by asyncio.run's cancel-all snapshot, and await_owned retains the join.
        identity=id(client)
        future=self._model_client_closes.get(identity)
        if future is None:
            if not any(existing is client for existing in self._owned_sync_clients):return
            context=contextvars.copy_context()
            future=asyncio.get_running_loop().run_in_executor(None,context.run,self._close_sync_model_client,client)
            self._model_client_closes[identity]=future
            def settled(completion):
                if self._model_client_closes.get(identity) is completion:
                    self._model_client_closes.pop(identity)
                if not completion.cancelled():completion.exception()
            future.add_done_callback(settled)
        await await_owned(future)

    async def _aclose_async_model_client(self,client):
        identity=id(client)
        completion=self._model_client_closes.get(identity)
        if completion is not None:
            return await await_owned(completion)
        if not any(existing is client for existing in self._owned_async_clients):return
        completion=asyncio.get_running_loop().create_future()
        self._model_client_closes[identity]=completion
        # Run on this existing owner task. An additional ordinary Task could be
        # cancelled by loop shutdown before it starts native settlement.
        try:
            owner=self._model_http_owners.get(identity)
            await (owner.aclose if owner is not None else client.close)()
            self._forget_model_client(client,self._owned_async_clients)
        except BaseException as error:
            completion.set_exception(error)
            completion.exception()  # Also safe when there was no second waiter.
            raise
        else:
            completion.set_result(None)
        finally:
            if self._model_client_closes.get(identity) is completion:
                self._model_client_closes.pop(identity)

    def _close_sync_resources(self,*,models=True):
        errors = []
        for identity, budget in list(self._session_token_budgets.items()):
            try:
                budget.close()
            except BaseException as error:
                errors.append(error)
            else:
                self._session_token_budgets.pop(identity, None)
        context = self._checkpointer_context
        if context is not None:
            context_errors = []
            try:
                context.__exit__(None, None, None)
            except BaseException as error:
                context_errors.append(error)
            connection = getattr(getattr(self, 'checkpointer', None), 'conn', None)
            if connection is not None:
                try:
                    connection.close()
                except BaseException as error:
                    context_errors.append(error)
            if not context_errors:
                self._checkpointer_context = None
            errors.extend(context_errors)
        for client in list(self._owned_sync_clients) if models else []:
            try:
                self._close_sync_model_client(client)
            except BaseException as error:
                errors.append(error)
        return errors

    async def _close_async_clients(self):
        errors = []
        for client in list(self._owned_async_clients):
            try:
                await self._aclose_async_model_client(client)
            except BaseException as error:
                errors.append(error)
        return errors

    async def _release_model(self, model):
        async def release():
            errors = []
            for name, clients in (('root_async_client', self._owned_async_clients),
                                  ('root_client', self._owned_sync_clients)):
                client = getattr(model, name, None)
                if client is None or not any(existing is client for existing in clients):
                    continue
                try:
                    if name == 'root_async_client':
                        await self._aclose_async_model_client(client)
                    else:
                        await self._aclose_sync_model_client(client)
                except BaseException as error:
                    errors.append(error)
            raise_cleanup_errors(errors)
        await await_owned(asyncio.create_task(release(), name='runtime:close-child-model'))

    def close(self):
        """Close a synchronous-only factory; used async resources require aclose."""
        manager = getattr(self, 'mcp_manager', None)
        if (self._async_checkpointer_context is not None or self._async_resource_lock.locked()
                or self._unpublished_savers or getattr(manager, 'has_live_connections', False)
                or self._resource_close_task is not None and not self._resource_close_task.done()
                or any(not operation.done() for operation in self._model_client_closes.values())
                or any(owner.asynchronous and owner.started for owner in self._model_http_owners.values())
                or self._async_resources_started and self._owned_async_clients):
            raise RuntimeError('Async runtime resources require await factory.aclose().')
        self._resources_closing = True
        errors = self._close_sync_resources()
        if self._owned_async_clients:
            # No async runtime has been admitted, so these SDK pools have never
            # been used by an async graph. They have no owner-loop transports.
            # Do not nest a loop in the caller's thread (constructor rollback
            # may itself run inside an event loop).
            def close_unused():
                return asyncio.run(self._close_async_clients())
            with ThreadPoolExecutor(max_workers=1) as worker:
                errors.extend(worker.submit(close_unused).result())
        raise_cleanup_errors(errors)

    async def _close_resources(self):
        errors = []
        manager = getattr(self, 'mcp_manager', None)
        if manager is not None:
            try:
                await manager.aclose()
            except BaseException as error:
                errors.append(error)
        async with self._async_resource_lock:
            for record in list(self._unpublished_savers):
                try:
                    await await_owned(start_sqlite_lifecycle(
                        self._close_unpublished_saver(record),name='runtime:sqlite-rollback'))
                except BaseException as error:
                    errors.append(error)
            context, saver = self._async_checkpointer_context, self.async_checkpointer
            if context is not None:
                try:
                    await await_owned(start_sqlite_lifecycle(
                        self._close_published_saver(context,saver),name='runtime:sqlite-close'))
                except BaseException as error:
                    errors.append(error)
        errors.extend(await self._close_async_clients())
        for client in list(self._owned_sync_clients):
            try:await self._aclose_sync_model_client(client)
            except BaseException as error:errors.append(error)
        errors.extend(self._close_sync_resources(models=False))
        raise_cleanup_errors(errors)

    async def aclose(self):
        task = self._resource_close_task
        if task is asyncio.current_task():
            raise RuntimeError('Runtime cleanup cannot wait for itself.')
        # Retry failed resources on a later explicit call; concurrent waiters
        # retain the same owner and observe that owner's original failure.
        if task is None or task.done() and (task.cancelled() or task.exception() is not None):
            self._resources_closing = True
            task = asyncio.create_task(self._close_resources(), name='runtime:close')
            self._resource_close_task = task
        await await_owned(task)
