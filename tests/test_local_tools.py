import os
import shlex
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import local_tools
from platform_fixtures import python_command


class LocalToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.project_root_patch = patch.object(local_tools, "PROJECT_ROOT", self.root)
        self.project_root_patch.start()

    def tearDown(self):
        self.project_root_patch.stop()
        self.temp_dir.cleanup()

    def test_read_file_limits_output_and_rejects_protected_paths(self):
        (self.root / "notes.txt").write_text("abcdef", encoding="utf-8", newline='\n')
        (self.root / ".env").write_text("private", encoding="utf-8", newline='\n')

        result = local_tools.read_file("notes.txt", max_chars=3)
        blocked = local_tools.read_file(".env")

        self.assertTrue(result["ok"])
        self.assertEqual(result["content"], "abc")
        self.assertTrue(result["truncated"])
        self.assertFalse(blocked["ok"])
        self.assertNotIn("private", str(blocked))

    def test_paths_reject_outside_targets_and_symlink_escapes(self):
        outside = Path(self.temp_dir.name).parent / (Path(self.temp_dir.name).name + "-outside.txt")
        outside.write_text("outside", encoding="utf-8", newline='\n')
        link = self.root / "escape.txt"
        link.symlink_to(outside)
        try:
            direct = local_tools.read_file(str(outside))
            linked = local_tools.read_file("escape.txt")
        finally:
            outside.unlink(missing_ok=True)

        self.assertFalse(direct["ok"])
        self.assertFalse(linked["ok"])

    def test_protected_symlink_alias_cannot_be_read_or_written(self):
        readme = self.root / "README.md"
        readme.write_text("keep", encoding="utf-8", newline='\n')
        (self.root / ".env").symlink_to(readme)

        read_result = local_tools.read_file(".env")
        write_result = local_tools.write_file(".env", "overwrite")

        self.assertFalse(read_result["ok"])
        self.assertFalse(write_result["ok"])
        self.assertEqual(readme.read_text(encoding="utf-8"), "keep")

    def test_protected_names_are_case_insensitive(self):
        for name in (".ENV", ".GIT", ".VENV", "__PYCACHE__", ".ENV.LOCAL"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                local_tools.resolve_project_path(name, allow_missing=True)

    def test_listing_search_and_write_stay_inside_project_root(self):
        source = self.root / "src"
        source.mkdir()
        (source / "main.py").write_text("answer = 42\n", encoding="utf-8", newline='\n')
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "hidden.py").write_text("answer = 0\n", encoding="utf-8", newline='\n')

        listing = local_tools.list_files(".")
        matches = local_tools.search_text("answer", "src")
        written = local_tools.write_file("out/result.txt", "done")
        escaped = local_tools.write_file("../escape.txt", "no")

        self.assertIn("src/main.py", listing["files"])
        self.assertNotIn(".venv/hidden.py", listing["files"])
        self.assertEqual(matches["matches"][0]["line_number"], 1)
        self.assertTrue(written["ok"])
        self.assertEqual((self.root / "out/result.txt").read_text(encoding="utf-8"), "done")
        self.assertFalse(escaped["ok"])

    def test_run_command_times_out_and_does_not_inherit_model_key(self):
        command = python_command("import os; print(os.getenv('DEEPSEEK_API_KEY', 'missing'))")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}):
            result = local_tools.run_command(command, timeout_seconds=5)

        timeout = local_tools.run_command(python_command('import time; time.sleep(2)'), timeout_seconds=1)
        failure_command = python_command("import sys; print('failed'); sys.exit(7)")
        failure = local_tools.run_command(failure_command, timeout_seconds=5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["output"].strip(), "missing")
        self.assertNotIn("test-secret", result["output"])
        self.assertTrue(timeout["timed_out"])
        self.assertLessEqual(len(timeout["output"]), local_tools.MAX_OUTPUT_CHARS)
        self.assertFalse(failure["ok"])
        self.assertEqual(failure["exit_code"], 7)
        self.assertIn("failed", failure["output"])

    def test_command_may_close_output_streams_before_exiting(self):
        script = "import os, time; os.close(1); os.close(2); time.sleep(0.2)"
        command = python_command(script)
        started = time.monotonic()

        result = local_tools.run_command(command, timeout_seconds=2)

        elapsed = time.monotonic() - started
        self.assertTrue(result["ok"])
        self.assertFalse(result["timed_out"])
        self.assertEqual(result["exit_code"], 0)
        self.assertGreaterEqual(elapsed, 0.15)

    def test_timeout_kills_child_processes_and_output_capture_is_bounded(self):
        marker = self.root / "child-finished.txt"
        child = "import time; from pathlib import Path; time.sleep(2); Path("+repr(str(marker))+").touch()"
        command = python_command("import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', "+repr(child)+"]); time.sleep(30)")
        timeout = local_tools.run_command(command, timeout_seconds=1)
        time.sleep(2.2)

        loud_command = python_command("print('x' * 30000)")
        loud = local_tools.run_command(loud_command, timeout_seconds=5)

        self.assertTrue(timeout["timed_out"])
        self.assertFalse(marker.exists())
        self.assertEqual(len(loud["output"]), local_tools.MAX_OUTPUT_CHARS)
        self.assertTrue(loud["output_truncated"])


if __name__ == "__main__":
    unittest.main()
