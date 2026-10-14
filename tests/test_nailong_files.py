from platform_fixtures import assert_private
import tempfile
import unittest
import os
import shutil
from pathlib import Path
from unittest.mock import patch

from nailong.tools.files import FileSession, MAX_READ_CHARS


class FileSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.session = FileSession(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write(self, relative_path="sample.txt", content="before\nafter\n"):
        target = self.root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
        return target

    def test_read_rejects_symlink_replacement_after_resolution(self):
        target = self.write('visible.txt', 'public')
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside)/'secret.txt'
            secret.write_text('synthetic-private-value', newline='\n')
            resolve = self.session.resolve
            def swapped(path, **kwargs):
                resolved = resolve(path, **kwargs)
                target.unlink()
                target.symlink_to(secret)
                return resolved
            with patch.object(self.session, 'resolve', side_effect=swapped):
                result = self.session.read_file('visible.txt')
            self.assertFalse(result['ok'])
            self.assertNotIn('synthetic-private-value', str(result))

    def test_read_rejects_parent_symlink_replacement_after_resolution(self):
        target = self.write('visible/data.txt', 'public')
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside)/'data.txt'
            secret.write_text('synthetic-private-value', newline='\n')
            resolve = self.session.resolve
            def swapped(path, **kwargs):
                resolved = resolve(path, **kwargs)
                target.parent.rename(self.root/'original')
                (self.root/'visible').symlink_to(Path(outside), target_is_directory=True)
                return resolved
            with patch.object(self.session, 'resolve', side_effect=swapped):
                result = self.session.read_file('visible/data.txt')
            self.assertFalse(result['ok'])
            self.assertNotIn('synthetic-private-value', str(result))

    @unittest.skipUnless(shutil.which('rg'), 'ripgrep required')
    def test_regex_cannot_query_symlink_swapped_during_backend_execution(self):
        target = self.write('visible.txt', 'public\n')
        secret = self.write('.env', 'synthetic-protected-sentinel\n')
        capture = self.session._bounded_search_output
        def swapped(command):
            target.unlink()
            target.symlink_to(secret)
            try:
                return capture(command)
            finally:
                target.unlink()
                target.write_text('public\n', newline='\n')
        with patch.object(self.session, '_bounded_search_output', side_effect=swapped):
            counts = self.session.grep('synthetic-protected-sentinel', path='visible.txt', output_mode='count')
            files = self.session.grep('synthetic-protected-sentinel', path='visible.txt', output_mode='files_with_matches')
        self.assertTrue(counts['ok'], counts)
        self.assertEqual(counts['total_matches'], 0)
        self.assertEqual(files['files'], [])

    @unittest.skipUnless(shutil.which('rg'), 'ripgrep required')
    def test_regex_marks_replaced_file_as_partial_before_snapshot(self):
        target = self.write('visible.txt', 'public\n')
        secret = self.write('.env', 'synthetic-protected-sentinel\n')
        search = self.session._ripgrep_matches
        def swapped(*args, **kwargs):
            target.unlink()
            target.symlink_to(secret)
            return search(*args, **kwargs)
        with patch.object(self.session, '_ripgrep_matches', side_effect=swapped):
            result = self.session.grep('synthetic-protected-sentinel', path='visible.txt', output_mode='count')
        if os.name == 'nt':
            self.assertFalse(result['ok'], result)
            self.assertNotIn('synthetic-protected-sentinel', str(result))
            return
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['total_matches'], 0)
        self.assertFalse(result['coverage_complete'])
        self.assertEqual(result['skipped_count'], 1)

    @unittest.skipUnless(shutil.which('rg'), 'ripgrep required')
    def test_regex_search_finds_valid_utf8_text_containing_nul(self):
        (self.root/'text.txt').write_bytes(b'hello\x00needle\n')
        result = self.session.grep('needle', path='text.txt')
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['matches']), 1)
        self.assertIn('needle', result['matches'][0]['text'])

    @unittest.skipIf(os.name == 'nt', 'POSIX umask; native Windows new-file DACL inheritance is tested separately.')
    def test_new_file_respects_restrictive_umask(self):
        previous = os.umask(0o077)
        try:
            result = self.session.write_file('private.txt', 'private')
        finally:
            os.umask(previous)
        self.assertTrue(result['ok'], result)
        assert_private(self, self.root/'private.txt')

    def test_edit_requires_file_to_be_read_in_this_session(self):
        self.write()
        result = self.session.edit_file("sample.txt", "before", "changed")
        self.assertFalse(result["ok"])
        self.assertEqual(result["hint"], "read_file")

    def test_edit_replaces_one_exact_match_and_returns_diff(self):
        target = self.write()
        self.session.read_file("sample.txt")
        result = self.session.edit_file("sample.txt", "before", "changed")
        self.assertTrue(result["ok"])
        self.assertEqual(target.read_text(), "changed\nafter\n")
        self.assertEqual(result["replacements"], 1)
        self.assertEqual(result["line_start"], 1)
        self.assertIn("-before", result["diff"])
        self.assertIn("+changed", result["diff"])

    def test_edit_reports_missing_match_without_changing_file(self):
        target = self.write()
        self.session.read_file("sample.txt")
        result = self.session.edit_file("sample.txt", "missing", "changed")
        self.assertFalse(result["ok"])
        self.assertEqual(target.read_text(), "before\nafter\n")

    def test_edit_rejects_duplicate_exact_matches_by_default(self):
        target = self.write(content="same\nsame\n")
        self.session.read_file("sample.txt")
        result = self.session.edit_file("sample.txt", "same", "new")
        self.assertFalse(result["ok"])
        self.assertIn("匹配不唯一", result["error"])
        self.assertEqual(target.read_text(), "same\nsame\n")

    def test_edit_can_replace_all_duplicate_exact_matches(self):
        target = self.write(content="same\nsame\n")
        self.session.read_file("sample.txt")
        result = self.session.edit_file("sample.txt", "same", "new", replace_all=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["replacements"], 2)
        self.assertEqual(target.read_text(), "new\nnew\n")

    def test_edit_normalizes_crlf_to_lf_and_preserves_original_newlines(self):
        target = self.write(content="first\r\nsecond\r\n")
        self.session.read_file("sample.txt")
        result = self.session.edit_file("sample.txt", "first\nsecond", "updated")
        self.assertTrue(result["ok"])
        self.assertTrue(result["fuzzy"])
        self.assertEqual(target.read_bytes(), b"updated\r\n")

    def test_edit_ignores_trailing_whitespace_when_normalizing(self):
        target = self.write(content="value = 1   \n")
        self.session.read_file("sample.txt")
        result = self.session.edit_file("sample.txt", "value = 1  \n", "value = 2\n")
        self.assertTrue(result["ok"])
        self.assertTrue(result["fuzzy"])
        self.assertEqual(target.read_text(), "value = 2\n")

    def test_edit_rejects_ambiguous_normalized_match(self):
        target = self.write(content="call(  a )\ncall( a  )\n")
        self.session.read_file("sample.txt")
        result = self.session.edit_file("sample.txt", "call( a )", "new")
        self.assertFalse(result["ok"])
        self.assertIn("匹配不唯一", result["error"])
        self.assertEqual(target.read_text(), "call(  a )\ncall( a  )\n")

    def test_edit_detects_external_content_change_after_read(self):
        target = self.write()
        self.session.read_file("sample.txt")
        previous_stat = target.stat()
        target.write_text("change\nafter\n", newline='\n')
        os.utime(target, ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns))
        result = self.session.edit_file("sample.txt", "before", "changed")
        self.assertFalse(result["ok"])
        self.assertIn("外部修改", result["error"])
        self.assertEqual(target.read_text(), "change\nafter\n")

    def test_edit_preserves_absence_of_final_newline(self):
        target = self.write(content="before")
        self.session.read_file("sample.txt")
        result = self.session.edit_file("sample.txt", "before", "after\n")
        self.assertTrue(result["ok"])
        self.assertEqual(target.read_bytes(), b"after")

    def test_edit_rejects_protected_path_even_when_symlink_points_inside_root(self):
        self.write("README.md", "safe")
        (self.root / ".env").symlink_to(self.root / "README.md")
        result = self.session.read_file(".env")
        self.assertFalse(result["ok"])
        self.assertNotIn("safe", str(result))

    def test_edit_noop_is_idempotent_and_does_not_replace_file(self):
        target = self.write()
        self.session.read_file("sample.txt")
        with patch("nailong.tools.files.os.replace") as replace:
            result = self.session.edit_file("sample.txt", "before", "before")
        self.assertTrue(result["ok"])
        self.assertEqual(result["replacements"], 0)
        replace.assert_not_called()
        self.assertEqual(target.read_text(), "before\nafter\n")

    def test_read_file_supports_one_based_line_offset_and_limit(self):
        self.write(content="one\ntwo\nthree\nfour\n")
        result = self.session.read_file("sample.txt", offset=2, limit=2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["content"], "two\nthree\n")
        self.assertEqual(result["next_offset"], 4)
        self.assertTrue(result["truncated"])

    def test_read_file_can_continue_through_a_line_larger_than_output_cap(self):
        self.write(content="x" * 25_000 + "tail\n")
        first = self.session.read_file("sample.txt")
        second = self.session.read_file(
            "sample.txt", offset=first["next_offset"], char_offset=first["next_char_offset"]
        )
        self.assertEqual(len(first["content"]), MAX_READ_CHARS)
        self.assertEqual(first["next_offset"], 1)
        self.assertEqual(first["next_char_offset"], MAX_READ_CHARS)
        self.assertEqual(len(second["content"]), MAX_READ_CHARS)
        self.assertEqual(second["next_char_offset"], MAX_READ_CHARS * 2)

    def test_grep_regex_without_ripgrep_reports_missing_backend(self):
        self.write(content="value = \\d+\nvalue = 42\n")
        with patch("nailong.tools.files.shutil.which", return_value=None):
            result = self.session.grep(r"value = \d+")
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_code'], 'backend_unavailable')
        self.assertEqual(result['pattern_mode'], 'regex')
        self.assertFalse(result['coverage_complete'])
        self.assertNotIn('matches', result)

    def test_grep_explicit_literal_without_ripgrep_preserves_literal_semantics(self):
        self.write(content="value = \\d+\nvalue = 42\n")
        with patch("nailong.tools.files.shutil.which", return_value=None):
            result = self.session.grep(r"value = \d+", pattern_mode='literal')
        self.assertTrue(result["ok"])
        self.assertEqual([item["line_number"] for item in result["matches"]], [1])
        self.assertEqual(result['backend'], 'python_literal')
        self.assertEqual(result['pattern_mode'], 'literal')

    def test_grep_backend_timeout_does_not_fall_back_to_python_regex(self):
        import subprocess

        self.write(content="a" * 50_000 + "!\n")
        with (
            patch("nailong.tools.files.shutil.which", return_value="/fake/rg"),
            patch.object(
                self.session, "_bounded_search_output",
                side_effect=subprocess.TimeoutExpired("rg", 5),
            ),
            patch.object(
                self.session,
                "_literal_matches",
                side_effect=AssertionError("unsafe fallback"),
            ),
        ):
            result = self.session.grep(r"(?=(a+)+$)a")
        self.assertFalse(result["ok"])
        self.assertEqual(result['error_code'], 'search_backend_failed')
        self.assertNotIn("matches", result)

    def test_glob_is_sorted_by_modification_time_and_skips_protected_paths(self):
        first = self.write("src/first.py")
        second = self.write("src/second.py")
        self.write(".env.local", "secret")
        os.utime(first, ns=(1_000_000_000, 1_000_000_000))
        os.utime(second, ns=(2_000_000_000, 2_000_000_000))
        result = self.session.glob("**/*.py")
        self.assertTrue(result["ok"])
        self.assertEqual(result["files"], ["src/second.py", "src/first.py"])
        self.assertNotIn(".env.local", result["files"])

    @unittest.skipUnless(shutil.which("rg"), "ripgrep is optional")
    def test_grep_supports_regex_include_context_and_output_modes(self):
        self.write("src/app.py", "before\nvalue = 42\nafter\n")
        self.write("src/readme.md", "value = 43\n")
        result = self.session.grep(
            r"value = \d+", path="src", include="*.py", context=1
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["line_number"], 2)
        self.assertEqual(result["matches"][0]["context_before"], ["before"])
        self.assertEqual(result["matches"][0]["context_after"], ["after"])
        files = self.session.grep(r"value = \d+", path="src", output_mode="files_with_matches")
        self.assertEqual(files["files"], ["src/app.py", "src/readme.md"])


if __name__ == "__main__":
    unittest.main()
