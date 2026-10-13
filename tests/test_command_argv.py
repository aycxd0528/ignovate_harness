"""Actual subprocess boundaries for command arguments and execution evidence."""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import local_tools
from nailong.core.processes import ProcessCancelled
from nailong.core.validation_output import command_test_output
from nailong.tools.results import ResultArchive
from platform_fixtures import python_command


class CommandArgvTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="command argv ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_process(self, **arguments):
        with local_tools.use_project_root(self.root):
            try:
                return local_tools.run_command(**arguments)
            except (TypeError, ValueError, AttributeError, OverflowError) as error:
                self.fail(f"Command argument contract is unavailable: {error}")

    def test_argv_preserves_quotes_empty_arguments_and_shell_metacharacters(self):
        # Break caught: joining argv into shell text changes literals or runs injected commands.
        arguments = ["", "has space", "a\"b", "尾\\", "$(touch injected)", "; touch injected"]
        result = self.run_process(argv=[sys.executable, "-c", "import json,sys;print(json.dumps(sys.argv[1:]))", *arguments])
        self.assertTrue(result["ok"], result)
        self.assertEqual(json.loads(result["output"]), arguments)
        self.assertFalse((self.root / "injected").exists())
        self.assertEqual(result["execution_mode"], "argv")
        self.assertFalse(result["shell"])
        self.assertTrue(result["started"])
        self.assertTrue(result["completed"])
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["exit_code_matches"])

    def test_actual_failure_is_not_masked_by_printed_success(self):
        # Break caught: deriving command success from echoed text rather than its returncode.
        result = self.run_process(argv=[sys.executable, "-c", "print('Ran 2 tests in 0.002s\\n\\nOK');raise SystemExit(7)"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["expected_exit_code"], 0)
        self.assertFalse(result["exit_code_matches"])
        self.assertFalse(command_test_output("run_command", result)["execution_verified"])

    def test_expected_nonzero_uses_actual_returncode(self):
        # Break caught: forcing nonzero exits to failure even for explicitly requested negative checks.
        result = self.run_process(argv=[sys.executable, "-c", "raise SystemExit(7)"], expected_exit_code=7)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["expected_exit_code"], 7)
        self.assertTrue(result["exit_code_matches"])

    def test_expected_nonzero_does_not_accept_unexpected_zero(self):
        # Break caught: retaining a zero-only success branch while claiming a negative check passed.
        result = self.run_process(argv=[sys.executable, "-c", "pass"], expected_exit_code=7)
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["exit_code_matches"])

    def test_shell_compatibility_reports_only_the_shell_exit(self):
        # Break caught: pretending the final shell status proves every earlier pipeline/subcommand.
        command = python_command("raise SystemExit(7)") + "; " + python_command("print('tail')")
        result = self.run_process(command=command)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result.get("execution_mode"), "shell")
        self.assertTrue(result["shell"])

    def test_legacy_capture_boundary_preserves_its_shell_result_tuple(self):
        # Break caught: removing the capture boundary breaks existing serialization instrumentation.
        self.assertTrue(hasattr(local_tools, "_capture_command_output"), "Legacy command capture boundary is unavailable")
        with local_tools.use_project_root(self.root):
            output, truncated, timed_out, exit_code = local_tools._capture_command_output(
                python_command("print('legacy');raise SystemExit(3)"), 3)
        self.assertEqual(output, "legacy\n")
        self.assertFalse(truncated)
        self.assertFalse(timed_out)
        self.assertEqual(exit_code, 3)

    def test_printed_runner_summary_is_only_an_output_observation(self):
        # Break caught: promoting a real process that only prints OK into proof that tests ran.
        result = self.run_process(command=python_command("print('Ran 2 tests in 0.002s\\n\\nOK')"))
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("completed"))
        evidence = command_test_output("run_command", result)
        self.assertEqual(evidence["status"], "reported_pass")
        self.assertFalse(evidence["execution_verified"])
        self.assertNotIn("acceptance_verified", result)

    def test_invalid_arguments_fail_before_a_process_starts(self):
        # Break caught: ambiguous, malformed or unbounded arguments execute user code.
        script = "from pathlib import Path;Path('started').touch()"
        valid_argv = [sys.executable, "-c", script]
        cases = [
            {"command": "echo started", "argv": valid_argv},
            {"command": " ", "argv": valid_argv},
            {"command": 42}, {"command": "echo\x00 started"},
            {"argv": []}, {"argv": "echo started"}, {"argv": [""]},
            {"argv": [sys.executable, 42]}, {"argv": [sys.executable, "\x00"]},
            {"argv": [sys.executable, "x" * 4001]},
            {"argv": [sys.executable] + [""] * 256},
            {"argv": valid_argv, "expected_exit_code": True},
            {"argv": valid_argv, "expected_exit_code": "7"},
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.run_process(**arguments)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result.get("error_code"), "invalid_parameters")
                self.assertFalse(result["started"])
                self.assertIsNone(result["exit_code"])
        self.assertFalse((self.root / "started").exists())

    def test_missing_executable_has_no_execution_evidence(self):
        # Break caught: a launch failure returns a fake returncode or started/completed proof.
        result = self.run_process(argv=[str(self.root / "missing-program")])
        self.assertFalse(result["ok"])
        self.assertFalse(result["started"])
        self.assertFalse(result["completed"])
        self.assertIsNone(result["exit_code"])
        self.assertFalse(result["exit_code_matches"])

    def archive_scope(self, archive):
        self.assertTrue(hasattr(local_tools, "use_command_result_archive"), "Command output archive scope is unavailable")
        return local_tools.use_command_result_archive(archive)

    def test_complete_archive_can_recover_truncated_output(self):
        # Break caught: archiving an already shortened preview loses the middle of the process log.
        archive = ResultArchive(self.root / "artifacts")
        with self.archive_scope(archive):
            result = self.run_process(argv=[sys.executable, "-c", "print('HEAD' + 'x'*30000 + 'TAIL')"])
        self.assertTrue(result["ok"])
        self.assertTrue(result["output_truncated"])
        self.assertLessEqual(len(result["output"]), local_tools.MAX_OUTPUT_CHARS)
        artifact = result["output_archive"]
        self.assertTrue(artifact["artifact_complete"])
        self.assertEqual(artifact["original_chars"], 30009)
        recovered = ""
        offset = 0
        while True:
            page = archive.read(artifact["reference"], offset=offset)
            self.assertTrue(page["ok"], page)
            recovered += page["content"]
            if not page["truncated"]:
                break
            offset = page["next_offset"]
        self.assertEqual(recovered, "HEAD" + "x" * 30000 + "TAIL\n")

    def test_archive_cap_is_explicit_about_missing_output(self):
        # Break caught: marking a bounded output prefix as the complete process transcript.
        archive = ResultArchive(self.root / "artifacts")
        with self.archive_scope(archive):
            result = self.run_process(argv=[sys.executable, "-c", "print('x'*1000050 + 'TAIL')"])
        artifact = result["output_archive"]
        self.assertFalse(artifact["artifact_complete"])
        self.assertEqual(artifact["original_chars"], 1000055)
        self.assertEqual(artifact["artifact_chars"], 1000000)
        self.assertIn("TAIL", result["output"])
        self.assertTrue(result["ok"])

    def test_archive_failure_does_not_change_the_actual_process_outcome(self):
        # Break caught: a failed archive is silently complete or rewrites an exit result.
        blocker = self.root / "blocked-archive"
        blocker.write_text("file", encoding="utf-8")
        archive = ResultArchive(blocker)
        with self.archive_scope(archive):
            result = self.run_process(argv=[sys.executable, "-c", "print('actual process output')"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["output_archive"]["artifact_complete"])
        self.assertNotIn("reference", result["output_archive"])
        self.assertIn("artifact_error", result["output_archive"])

    def test_missing_archive_is_explicit_and_scope_does_not_leak(self):
        # Break caught: a prior invocation's capability becomes readable in a different context.
        archive = ResultArchive(self.root / "artifacts")
        with self.archive_scope(archive):
            archived = self.run_process(argv=[sys.executable, "-c", "print('first')"])
        result = self.run_process(argv=[sys.executable, "-c", "print('second')"])
        self.assertIn("reference", archived["output_archive"])
        self.assertFalse(result["output_archive"]["artifact_complete"])
        self.assertNotIn("reference", result["output_archive"])
        self.assertIn("artifact_error", result["output_archive"])

    def test_streamed_secrets_are_redacted_from_preview_and_archive(self):
        # Break caught: a credential crossing a pipe read survives either bounded preview or full archive.
        secret = "fake-private-key-very-long-sentinel"
        code = ("import os,time;"
                f"os.write(1, b'x'*4090 + {secret[:10].encode()!r});"
                "time.sleep(.04);"
                f"os.write(1, {secret[10:].encode()!r}+b'\\nEND\\n')")
        archive = ResultArchive(self.root / "artifacts", api_key=secret)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": secret}), self.archive_scope(archive):
            result = self.run_process(argv=[sys.executable, "-c", code])
        self.assertTrue(result["ok"])
        self.assertNotIn(secret[:10], result["output"])
        self.assertIn("[密钥已隐藏]", result["output"])
        page = archive.read(result["output_archive"]["reference"])
        self.assertTrue(page["artifact_complete"])
        self.assertNotIn(secret[:10], page["content"])
        self.assertIn("[密钥已隐藏]", page["content"])

    def test_timeout_stops_argv_descendants_even_with_matching_kill_status(self):
        # Break caught: shell=False loses process-tree cleanup, or an expected kill code makes timeout OK.
        child = "import time;from pathlib import Path;time.sleep(1.4);Path('late').touch()"
        code = f"import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',{child!r}]);print('started',flush=True);time.sleep(30)"
        result = self.run_process(argv=[sys.executable, "-c", code], timeout_seconds=1, expected_exit_code=-9)
        self.assertTrue(result["started"])
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["completed"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code_matches"], result["exit_code"] == -9)
        time.sleep(.6)
        self.assertFalse((self.root / "late").exists())

    def test_nonfinite_timeout_cannot_launch_before_validation(self):
        # Break caught: converting the timeout after Popen leaks a process when conversion fails.
        result = self.run_process(argv=[sys.executable, "-c", "from pathlib import Path;Path('started').touch()"], timeout_seconds=float("inf"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "invalid_parameters")
        self.assertFalse(result["started"])
        time.sleep(.1)
        self.assertFalse((self.root / "started").exists())


class AsyncCommandArgvTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="async command argv ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    async def run_process(self, **arguments):
        self.assertTrue(hasattr(local_tools, "run_command_async"), "Cancellable command argv execution is unavailable")
        with local_tools.use_project_root(self.root):
            return await local_tools.run_command_async(**arguments)

    async def test_async_argv_preserves_literals_and_expected_nonzero(self):
        # Break caught: the runtime's cancellable handler still uses a shell or zero-only success.
        result = await self.run_process(argv=[sys.executable, "-c", "import sys;print(sys.argv[1]);raise SystemExit(7)", "$(touch injected)"], expected_exit_code=7)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["output"], "$(touch injected)\n")
        self.assertFalse((self.root / "injected").exists())
        self.assertEqual(result["execution_mode"], "argv")
        self.assertEqual(result["exit_code"], 7)

    async def test_repeated_cancellation_joins_descendants_before_return(self):
        # Break caught: the coroutine returns before owned descendants can no longer write.
        child = ("import signal,time;from pathlib import Path;"
                 "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                 "Path('child-ready').touch();time.sleep(.8);Path('late').touch();time.sleep(30)")
        code = f"import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',{child!r}]);print('started',flush=True);time.sleep(30)"
        operation = asyncio.create_task(self.run_process(argv=[sys.executable, "-c", code]))
        try:
            async with asyncio.timeout(3):
                while not (self.root / "child-ready").exists():
                    if operation.done():
                        await operation
                    await asyncio.sleep(.01)
            operation.cancel("first")
            await asyncio.sleep(0)
            operation.cancel("repeated")
            with self.assertRaises(ProcessCancelled) as caught:
                await operation
            result = caught.exception.result
            self.assertTrue(result["cancelled"])
            self.assertTrue(result["started"])
            self.assertFalse(result["completed"])
            self.assertFalse(result["ok"])
            self.assertIsInstance(result["exit_code"], int)
            await asyncio.sleep(.9)
            self.assertFalse((self.root / "late").exists())
        finally:
            if not operation.done():
                operation.cancel()
                try:
                    await operation
                except asyncio.CancelledError:
                    pass


if __name__ == "__main__":
    unittest.main()
