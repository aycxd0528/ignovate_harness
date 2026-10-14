"""Run native CI tests with useful diagnostics for an async test that stalls."""
from __future__ import annotations

import asyncio
import faulthandler
import inspect
import os
from pathlib import Path
import sys
import threading
import unittest

__unittest = True


def dump_tasks(test_name: str, *, stream=None) -> None:
    stream = sys.stderr if stream is None else stream
    print(f'\nAsync test still running: {test_name}', file=stream, flush=True)
    for task in sorted(asyncio.all_tasks(), key=lambda item: item.get_name()):
        print(repr(task), file=stream)
        coroutine = task.get_coro()
        seen = set()
        while coroutine is not None and id(coroutine) not in seen:
            seen.add(id(coroutine))
            frame = getattr(coroutine, 'cr_frame', None) or getattr(coroutine, 'gi_frame', None)
            if frame is not None:
                print(f'  {frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}',
                      file=stream)
            coroutine = getattr(coroutine, 'cr_await', None) or getattr(coroutine, 'gi_yieldfrom', None)
    stream.flush()


class NativeTestResult(unittest.TextTestResult):
    timeout_seconds = 120

    def _clean_tracebacks(self, exctype, value, tb, test):
        # The async monitor inserts runner frames between asyncio and test
        # frames; unittest's prefix pruning can discard the actual assertion.
        return tb

    def startTest(self, test):
        super().startTest(test)
        self._active_test = test
        self._watchdog = threading.Timer(self.timeout_seconds, self._stalled, args=(test,))
        self._watchdog.daemon = True
        self._watchdog.start()

    def stopTest(self, test):
        self._active_test = None
        self._watchdog.cancel()
        self._watchdog.join()
        super().stopTest(test)

    def _stalled(self, test):
        if self._active_test is not test:
            return
        print(f'\nNative test exceeded {self.timeout_seconds} seconds: {test.id()}',
              file=self.stream, flush=True)
        try:
            # The normal call holds the GIL while inspecting frame metadata.
            # The C watchdog used by dump_traceback_later races live frames in
            # CPython 3.11, so healthy UI tests must not receive periodic dumps.
            faulthandler.dump_traceback(file=self.stream, all_threads=True)
        finally:
            self.stream.flush()
            os._exit(1)


def install_async_diagnostics(*, stream=None) -> None:
    diagnostic_stream = sys.stderr if stream is None else stream
    original = unittest.IsolatedAsyncioTestCase._callTestMethod

    def call_test_method(self, method):
        if not inspect.iscoroutinefunction(method):
            return original(self, method)

        async def monitored():
            loop = asyncio.get_running_loop()

            def stalled():
                dump_tasks(self.id(), stream=diagnostic_stream)
                print('Async test exceeded 120 seconds; failing native CI.', file=diagnostic_stream,
                      flush=True)
                os._exit(1)

            report = loop.call_later(45, lambda: dump_tasks(self.id(), stream=diagnostic_stream))
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
    stream = sys.stderr
    install_async_diagnostics(stream=stream)
    suite = unittest.defaultTestLoader.discover(str(root / 'tests'))
    return int(not unittest.TextTestRunner(verbosity=2, stream=stream,
        resultclass=NativeTestResult).run(suite).wasSuccessful())


if __name__ == '__main__':
    raise SystemExit(main())
