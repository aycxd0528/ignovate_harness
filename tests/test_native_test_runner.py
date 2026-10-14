"""Native runner diagnostics survive UI capture and retain real failure locations."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


class NativeTestRunnerTests(unittest.TestCase):
    def run_fixture(self, source):
        process = subprocess.run([sys.executable, '-c', source],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def test_async_failure_traceback_includes_the_real_test_method(self):
        # Break caught: an unmarked wrapper makes unittest strip the actual assertion frame.
        result = self.run_fixture('''
import io, json, unittest
from scripts import run_native_tests as runner
class Failing(unittest.IsolatedAsyncioTestCase):
    async def test_actual_failure(self):
        self.assertTrue(False, 'fixture assertion')
runner.install_async_diagnostics()
output = io.StringIO()
unittest.TextTestRunner(stream=output, resultclass=runner.NativeTestResult).run(
    unittest.TestSuite([Failing('test_actual_failure')]))
print(json.dumps(output.getvalue()))
''')
        self.assertIn('in test_actual_failure', result)
        self.assertIn('fixture assertion', result)

    def test_async_task_dump_reaches_runner_stream_during_ui_stderr_capture(self):
        # Break caught: Textual's stderr redirection swallows stalled-test diagnostics.
        result = self.run_fixture('''
import asyncio, contextlib, io, json, unittest
from unittest.mock import patch
from scripts import run_native_tests as runner
output, ui_capture = io.StringIO(), io.StringIO()
class Running(unittest.IsolatedAsyncioTestCase):
    async def test_ui_capture(self):
        with contextlib.redirect_stderr(ui_capture):
            await asyncio.sleep(.02)
call_later = asyncio.BaseEventLoop.call_later
def fast_report(loop, delay, callback, *args, **kwargs):
    return call_later(loop, .001 if delay == 45 else delay, callback, *args, **kwargs)
with contextlib.redirect_stderr(output):
    runner.install_async_diagnostics()
    with patch('asyncio.BaseEventLoop.call_later', fast_report):
        unittest.TextTestRunner(stream=output).run(unittest.TestSuite([Running('test_ui_capture')]))
print(json.dumps({'runner': output.getvalue(), 'ui': ui_capture.getvalue()}))
''')
        self.assertIn('Async test still running:', result['runner'])
        self.assertNotIn('Async test still running:', result['ui'])

    def test_watchdog_deadline_applies_to_each_test_in_a_healthy_suite(self):
        # Break caught: a suite-level timer dumps or terminates healthy later tests.
        result = self.run_fixture('''
import io, json, time, unittest
from scripts import run_native_tests as runner
class Healthy(unittest.TestCase):
    def runTest(self):
        time.sleep(.1)
runner.NativeTestResult.timeout_seconds = .5
output = io.StringIO()
result = unittest.TextTestRunner(stream=output, resultclass=runner.NativeTestResult).run(
    unittest.TestSuite(Healthy() for _ in range(7)))
print(json.dumps({'success': result.wasSuccessful(), 'count': result.testsRun}))
''')
        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 7)

    def test_stalled_test_watchdog_reports_to_retained_stream_and_exits(self):
        # Break caught: a blocked loop prevents the test deadline or UI capture hides its diagnostic.
        process = subprocess.run([sys.executable, '-c', '''
import contextlib, io, time, unittest
from scripts import run_native_tests as runner
class Blocked(unittest.TestCase):
    def runTest(self):
        with contextlib.redirect_stderr(io.StringIO()):
            time.sleep(10)
runner.NativeTestResult.timeout_seconds = .05
unittest.TextTestRunner(resultclass=runner.NativeTestResult).run(unittest.TestSuite([Blocked()]))
'''], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=5)
        self.assertEqual(process.returncode, 1)
        self.assertIn('Native test exceeded', process.stderr)
        self.assertIn('Blocked.runTest', process.stderr)
        self.assertIn('in runTest', process.stderr)


if __name__ == '__main__':
    unittest.main()
