"""An execution lease shared by CLI and GUI, independent of conversation IDs."""
from contextlib import contextmanager
from contextvars import ContextVar
import asyncio
import threading

from nailong.core.file_locks import file_lock

_held = ContextVar('ignovate_project_execution', default=None)


@contextmanager
def project_execution(store):
    if store is None:
        yield
        return
    try:
        owner = asyncio.current_task()
    except RuntimeError:
        owner = threading.get_ident()
    identity = (str(store.root), owner)
    if _held.get() == identity:
        yield
        return
    with file_lock(store.root / 'project-execution.lock', blocking=False):
        token = _held.set(identity)
        try:
            yield
        finally:
            _held.reset(token)
