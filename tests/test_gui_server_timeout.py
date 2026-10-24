"""Timed out HTTP callers must release work still owned by the server loop."""
import threading
import unittest

import test_gui_server as fixtures


class ServerTimeoutTests(unittest.TestCase):
    setUp = fixtures.ServerTests.setUp
    tearDown = fixtures.ServerTests.tearDown

    def test_timeout_cancels_and_joins_the_owned_scan(self):
        from nailong.core.input_snapshot import active_snapshot_cancellation
        from nailong.core.verification import _owned_worker
        entered = threading.Event()
        released = threading.Event()
        cleaned = threading.Event()
        cancelled = threading.Event()

        def scan():
            cancellation = active_snapshot_cancellation.get()
            entered.set()
            while not released.is_set() and not cancellation.wait(.01):
                pass
            if cancellation.is_set():
                cancelled.set()

        async def observe():
            try:
                await _owned_worker(scan)
            finally:
                cleaned.set()

        try:
            with self.assertRaises(TimeoutError):
                self.server.call(observe, timeout=.05)
            self.assertTrue(entered.is_set())
            self.assertTrue(cleaned.wait(.5), 'timed out caller left its scan running')
            self.assertTrue(cancelled.is_set())
            self.assertEqual(self.server.call(lambda: 'responsive'), 'responsive')
        finally:
            released.set()
            cleaned.wait(2)
