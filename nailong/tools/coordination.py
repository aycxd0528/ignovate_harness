"""A project-scoped mutation gate shared by threads and asynchronous tools."""

from __future__ import annotations

import asyncio
import threading
import weakref
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

active_file_version: ContextVar[dict | None] = ContextVar("tool_approved_file_version", default=None)
active_read_permission: ContextVar[object | None] = ContextVar("tool_read_permission", default=None)


class MutationCoordinator:
    """Async acquisition never blocks the loop or leaves a cancelled waiter owning a lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self._local = threading.local()

    def __enter__(self):
        depth = getattr(self._local, "depth", 0)
        if depth == 0:
            self._lock.acquire()
        self._local.depth = depth + 1
        return self

    def __exit__(self, *exc):
        self._local.depth -= 1
        if self._local.depth == 0:
            self._lock.release()

    @asynccontextmanager
    async def async_scope(self):
        # No background acquisition thread: cancellation cannot orphan ownership.
        while not self._lock.acquire(blocking=False):
            await asyncio.sleep(0.01)
        try:
            yield self
        finally:
            self._lock.release()


_coordinators: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
_registry_lock = threading.Lock()


def project_coordinator(root: str | Path) -> MutationCoordinator:
    key = str(Path(root).resolve())
    with _registry_lock:
        coordinator = _coordinators.get(key)
        if coordinator is None:
            coordinator = MutationCoordinator()
            _coordinators[key] = coordinator
        return coordinator
