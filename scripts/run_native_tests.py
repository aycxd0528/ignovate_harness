"""Run native CI tests with useful diagnostics for an async test that stalls."""
from __future__ import annotations

import asyncio
import faulthandler
import inspect
import os
from pathlib import Path
import sys
import unittest


def dump_tasks(test_name: str) -> None:
    print(f'\nAsync test still running: {test_name}', file=sys.stderr, flush=True)
    for task in sorted(asyncio.all_tasks(), key=lambda item: item.get_name()):
        print(repr(task), file=sys.stderr)
        coroutine = task.get_coro()
        seen = set()
        while coroutine is not None and id(coroutine) not in seen:
            seen.add(id(coroutine))
            frame = getattr(coroutine, 'cr_frame', None) or getattr(coroutine, 'gi_frame', None)
            if frame is not None:
                print(f'  {frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}',
                      file=sys.stderr)
            coroutine = getattr(coroutine, 'cr_await', None) or getattr(coroutine, 'gi_yieldfrom', None)
    sys.stderr.flush()


def install_async_diagnostics() -> None:
    original = unittest.IsolatedAsyncioTestCase._callTestMethod

    def call_test_method(self, method):
        if not inspect.iscoroutinefunction(method):
            return original(self, method)

        async def monitored():
            loop = asyncio.get_running_loop()

            def stalled():
                dump_tasks(self.id())
                print('Async test exceeded 120 seconds; failing native CI.', file=sys.stderr,
                      flush=True)
                os._exit(1)

            report = loop.call_later(45, dump_tasks, self.id())
            deadline = loop.call_later(120, stalled)
            try:
                return await method()
            finally:
                report.cancel()
                deadline.cancel()

        return original(self, monitored)

    unittest.IsolatedAsyncioTestCase._callTestMethod = call_test_method


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    install_async_diagnostics()
    faulthandler.dump_traceback_later(120, repeat=True)
    try:
        suite = unittest.defaultTestLoader.discover(str(root / 'tests'))
        return int(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
    finally:
        faulthandler.cancel_dump_traceback_later()


if __name__ == '__main__':
    raise SystemExit(main())
