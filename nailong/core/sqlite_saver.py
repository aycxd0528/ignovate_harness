"""Own native SQLite and worker shutdown before the SDK context can fail."""
import asyncio
from contextlib import asynccontextmanager
import sqlite3

import aiosqlite
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from nailong.core.checkpoint_serde import CheckpointSerializer


class RuntimeSqliteSaver(SqliteSaver):
    def __init__(self, conn, *, serde=None):
        super().__init__(conn, serde=CheckpointSerializer() if serde is None else serde)


class _OwnedConnection(aiosqlite.Connection):
    def __init__(self,database):
        self._owned_native=None;self._owned_native_closed=False
        self._owned_stop_requested=False;self._owned_stop_completion=None
        def connect():
            connection=sqlite3.connect(database)
            self._owned_native=connection
            return connection
        # Keep the SDK's default chunking, SQLite options, and native worker.
        super().__init__(connect,iter_chunk_size=64)
        self._owned_worker=getattr(self,'_thread',self)

    def stop(self):
        # _connect may call stop before __aenter__ has yielded a saver. Retain
        # that completion and never enqueue another stop on a dead worker.
        if not self._owned_stop_requested:
            self._owned_stop_requested=True
            self._owned_stop_completion=super().stop()
        return self._owned_stop_completion

    async def close(self):
        if self._owned_native is not None and not self._owned_native_closed:
            # SDK close clears its native pointer/stops the worker even if the
            # queued close fails. First verify native close on its original
            # worker; a failure leaves both the handle and worker retryable.
            await self._execute(self._owned_native.close)
            self._owned_native_closed=True
        try:
            await super().close()
        finally:
            # A failed connect (or interrupted thread start) has no published
            # saver, but its worker still belongs to this connection context.
            if self._owned_worker.is_alive() and not self._owned_stop_requested:
                self.stop()
            if self._owned_stop_completion is not None:
                await self._owned_stop_completion
            while self._owned_worker.is_alive():
                await asyncio.sleep(.001)


class OwnedAsyncSqliteSaver(AsyncSqliteSaver):
    def __init__(self, conn, *, serde=None):
        super().__init__(conn, serde=CheckpointSerializer() if serde is None else serde)

    @classmethod
    @asynccontextmanager
    async def from_conn_string(cls,conn_string):
        connection=_OwnedConnection(conn_string)
        try:
            await connection
            yield cls(connection)
        finally:
            await connection.close()
