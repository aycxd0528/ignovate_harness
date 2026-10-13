"""Command output observations must not inherit a compound shell's success."""

import json
import importlib.util
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

from langchain_core.messages import ToolMessage

import local_tools
from agent_service import AgentService


class CommandTestEvidenceTests(unittest.TestCase):
    def summary(self, output, *, name="run_command", **overrides):
        payload = {"ok": True, "exit_code": 0, "timed_out": False,
                   "output_truncated": False, "output": output, "started": True}
        payload.update(overrides)
        message = ToolMessage(content=json.dumps(payload), name=name, tool_call_id="command-1")
        return AgentService(lambda **kwargs: None)._tool_result_summary(message)

    def test_real_failed_unittest_survives_successful_following_command(self):
        # Break caught: summarizing only shell success or the last output lines hides FAILED.
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "check.py").write_text(
                "import unittest\n"
                "class Check(unittest.TestCase):\n"
                "    def test_expected(self): self.assertEqual(1, 2)\n"
                "if __name__ == '__main__': unittest.main()\n", encoding="utf-8")
            python = shlex.quote(sys.executable)
            command = f"{python} check.py; {python} -c " + shlex.quote(
                "print('report one\\nreport two\\nreport three\\nreport four')")
            with local_tools.use_project_root(directory):
                payload = local_tools.run_command(command, timeout_seconds=5)
        message = ToolMessage(content=json.dumps(payload), name="run_command", tool_call_id="command-1")
        summary = AgentService(lambda **kwargs: None)._tool_result_summary(message)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["exit_code"], 0)
        self.assertNotIn("FAILED", summary["output_snippet"])
        evidence = summary.get("test_output", {})
        self.assertEqual(evidence.get("status"), "reported_failure")
        self.assertTrue(evidence["failure_observed"])
        self.assertEqual(evidence["source"], "run_command.output")
        self.assertEqual(evidence["summaries"], [
            {"framework": "unittest", "status": "failed", "tests_run": 1,
             "counts": {"failures": 1}}])

    def test_standard_terminal_summaries_are_observations_with_literal_counts(self):
        # Break caught: missing standard formats, conflating zero tests or collection errors with pass.
        cases = [
            ("Ran 2 tests in 0.002s\n\nFAILED (failures=2)\n", "reported_failure",
             {"framework": "unittest", "status": "failed", "tests_run": 2, "counts": {"failures": 2}}),
            ("Ran 3 tests in 0.002s\n\nOK (skipped=1)\n", "reported_pass",
             {"framework": "unittest", "status": "passed", "tests_run": 3, "counts": {"skipped": 1}}),
            ("Ran 0 tests in 0.000s\n\nOK\n", "zero_tests",
             {"framework": "unittest", "status": "zero_tests", "tests_run": 0, "counts": {}}),
            ("================ 1 failed, 2 passed in 0.03s ================\n", "reported_failure",
             {"framework": "pytest", "status": "failed", "counts": {"failed": 1, "passed": 2}}),
            ("2 passed, 1 warning in 0.03s\n", "reported_pass",
             {"framework": "pytest", "status": "passed", "counts": {"passed": 2, "warning": 1}}),
            ("================ 1 error in 0.03s ================\n", "reported_failure",
             {"framework": "pytest", "status": "failed", "counts": {"error": 1}}),
            ("no tests ran in 0.01s\n", "zero_tests",
             {"framework": "pytest", "status": "zero_tests", "counts": {}}),
        ]
        for output, status, observation in cases:
            with self.subTest(output=output):
                evidence = self.summary(output).get("test_output", {})
                self.assertEqual(evidence.get("status"), status)
                self.assertEqual(evidence["summaries"], [observation])
                self.assertFalse(evidence["ambiguous"])
                self.assertFalse(evidence["truncated"])

    def test_document_prose_and_noncommand_payloads_do_not_claim_test_execution(self):
        # Break caught: keyword scanning or scanning read-file/MCP data as command validation.
        prose = [
            "README: unittest may print FAILED (failures=2).",
            "The result should be 2 passed in 0.03s after you run tests.",
            "FAILED (failures=2)\n", "Ran 2 tests\nOK\n",
            "Ran 2 tests in 0.002s\n[output truncated]\nOK\n",
            "2 passed in 0.03s additional instructions\n",
            "```console\n2 passed in 0.03s\n```\n",
            "```\nRan 2 tests in 0.002s\n\nOK\n```\n",
        ]
        for output in prose:
            with self.subTest(output=output):
                evidence = self.summary(output).get("test_output", {})
                self.assertEqual(evidence.get("status"), "unknown")
                self.assertEqual(evidence["summaries"], [])
        for name in ("read_file", "mcp__docs__read", "read_tool_result"):
            with self.subTest(name=name):
                self.assertNotIn("test_output", self.summary("Ran 2 tests in 0.002s\n\nOK\n", name=name))
        self.assertNotIn("test_output", self.summary("Ran 2 tests in 0.002s\n\nOK\n", started=False))
        self.assertNotIn("test_output", self.summary("Ran 2 tests in 0.002s\n\nOK\n", exit_code=None))

    def test_exact_stdout_spoof_is_labeled_as_output_only(self):
        # Break caught: promoting syntactically valid stdout to executed-test or acceptance proof.
        python = shlex.quote(sys.executable)
        command = f"{python} -c " + shlex.quote("print('Ran 2 tests in 0.002s\\n\\nOK')")
        with tempfile.TemporaryDirectory() as directory, local_tools.use_project_root(directory):
            payload = local_tools.run_command(command, timeout_seconds=5)
        message = ToolMessage(content=json.dumps(payload), name="run_command", tool_call_id="spoof")
        evidence = AgentService(lambda **kwargs: None)._tool_result_summary(message).get("test_output", {})
        self.assertEqual(evidence.get("source"), "run_command.output")
        self.assertEqual(evidence["status"], "reported_pass")
        self.assertFalse(evidence["execution_verified"])

    def test_truncated_success_and_multiple_runs_cannot_report_an_unambiguous_pass(self):
        # Break caught: treating a retained tail or final rerun as proof of the whole command's tests.
        passed = "Ran 2 tests in 0.002s\n\nOK\n"
        failed = "Ran 2 tests in 0.002s\n\nFAILED (failures=1)\n"
        for output, overrides, failure in [
            (passed, {"output_truncated": True}, False),
            (passed, {"result_truncated": True}, False),
            (passed, {"truncated": True}, False),
            (passed, {"timed_out": True, "ok": False, "exit_code": -9}, False),
            (passed, {"ok": False, "exit_code": 1}, False),
            (passed + passed, {}, False),
            (failed + passed, {}, True),
        ]:
            with self.subTest(output=output, overrides=overrides):
                evidence = self.summary(output, **overrides).get("test_output", {})
                self.assertEqual(evidence.get("status"), "reported_failure" if failure else "unknown")
                self.assertEqual(evidence["failure_observed"], failure)
                self.assertEqual(evidence["ambiguous"], output == passed + passed or output == failed + passed)
        evidence = self.summary(failed, output_truncated=True).get("test_output", {})
        self.assertEqual(evidence.get("status"), "reported_failure")
        self.assertTrue(evidence["truncated"])

    def test_completed_pass_does_not_cover_a_later_incomplete_unittest_summary(self):
        # Break caught: treating an earlier pass as the result of an unfinished later run.
        output = "Ran 2 tests in 0.002s\n\nOK\nRan 2 tests in 0.003s\n"
        evidence = self.summary(output).get("test_output", {})
        self.assertEqual(evidence.get("status"), "unknown")
        self.assertTrue(evidence["incomplete"])
        self.assertTrue(evidence["ambiguous"])
        self.assertEqual(evidence["summary_count"], 1)

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "real pytest boundary requires pytest")
    def test_real_completed_pass_cannot_hide_a_later_pytest_invocation_error(self):
        # Break caught: an earlier unittest pass hides pytest failing before tests can run.
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "test_pass.py").write_text(
                "import unittest\n"
                "class Check(unittest.TestCase):\n"
                "    def test_expected(self): self.assertEqual(1, 1)\n"
                "if __name__ == '__main__': unittest.main()\n", encoding="utf-8")
            python = shlex.quote(sys.executable)
            command = (f"{python} test_pass.py; {python} -m pytest "
                       "--this-argument-is-intentionally-invalid; "
                       f"{python} -c " + shlex.quote("print('done')"))
            with local_tools.use_project_root(directory):
                payload = local_tools.run_command(command, timeout_seconds=5)
        self.assertIn("python -m pytest: error: unrecognized arguments:", payload["output"])
        message = ToolMessage(content=json.dumps(payload), name="run_command", tool_call_id="pytest-error")
        summary = AgentService(lambda **kwargs: None)._tool_result_summary(message)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["exit_code"], 0)
        evidence = summary.get("test_output", {})
        self.assertEqual(evidence.get("status"), "unknown")
        self.assertTrue(evidence["invocation_error_observed"])
        self.assertTrue(evidence["incomplete"])
        self.assertTrue(evidence["ambiguous"])
        self.assertFalse(evidence["execution_verified"])

    def test_pytest_invocation_errors_require_runner_lines_outside_documentation(self):
        # Break caught: missing canonical invocation errors or scanning quoted README prose.
        passed = "Ran 1 test in 0.001s\n\nOK\n"
        cases = [
            ("ERROR: usage: pytest [options] [file_or_dir] [file_or_dir] [...]\n", True),
            ("pytest: error: unrecognized arguments: --invalid\n", True),
            ("python -m pytest: error: unrecognized arguments: --invalid\n", True),
            ("README: pytest: error: unrecognized arguments: --invalid\n", False),
            ("The log includes ERROR: usage: pytest [options] and is only an example.\n", False),
            ("```console\nERROR: usage: python -m pytest [options] [file_or_dir] [file_or_dir] [...]\n"
             "python -m pytest: error: unrecognized arguments: --invalid\n```\n", False),
        ]
        for output, observed in cases:
            with self.subTest(output=output):
                evidence = self.summary(passed + output).get("test_output", {})
                self.assertEqual(evidence.get("status"), "unknown" if observed else "reported_pass")
                self.assertEqual(evidence.get("invocation_error_observed"), observed)
                self.assertEqual(evidence["incomplete"], observed)
                self.assertFalse(evidence["execution_verified"])


if __name__ == "__main__":
    unittest.main()
