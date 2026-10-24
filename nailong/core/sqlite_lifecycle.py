"""SQLite resource lifecycles that survive direct event-loop shutdown cancellation."""
import asyncio


class _SQLiteLifecycleTask(asyncio.Task):
    def cancel(self,msg=None):
        # A queued native open/close cannot be withdrawn safely. Its caller
        # owns cancellation separately and must join this resource operation.
        # In particular asyncio.run cancels all Tasks, bypassing shield().
        return False


def start_sqlite_lifecycle(operation,*,name):
    """Internal SQLite only: never wrap model/MCP work or SDK deadline scopes."""
    return _SQLiteLifecycleTask(operation,loop=asyncio.get_running_loop(),name=name)
