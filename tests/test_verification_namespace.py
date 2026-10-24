"""A project identity includes its native directory and internal link graph."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import local_tools
from nailong.core.verification import input_fingerprint


class NamespaceFingerprintTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()

    def test_empty_directory_addition_and_removal_change_identity(self):
        before = input_fingerprint(self.root)
        (self.root / 'assets').mkdir()
        added = input_fingerprint(self.root)
        self.assertNotEqual(added, before)
        (self.root / 'assets').rmdir()
        self.assertEqual(input_fingerprint(self.root), before)

    @unittest.skipIf(os.name == 'nt', 'POSIX directory modes; Windows ACLs need native validation.')
    def test_root_and_nested_directory_permissions_are_bound(self):
        directory = self.root / 'assets'
        directory.mkdir(mode=0o700)
        (directory / 'input').write_bytes(b'input')
        before = input_fingerprint(self.root)
        directory.chmod(0o755)
        nested = input_fingerprint(self.root)
        self.assertNotEqual(nested, before)
        self.root.chmod(0o755)
        self.assertNotEqual(input_fingerprint(self.root), nested)

    def test_empty_directory_added_during_scan_is_not_a_mixed_proof(self):
        (self.root / 'input').write_bytes(b'input')
        native = local_tools.open_regular_file

        @contextmanager
        def mutate(*args, **kwargs):
            (self.root / 'late-directory').mkdir(exist_ok=True)
            with native(*args, **kwargs) as stream:
                yield stream

        with patch('local_tools.open_regular_file', mutate):
            with self.assertRaisesRegex(ValueError, '修改|变化'):
                input_fingerprint(self.root)

    @unittest.skipIf(os.name == 'nt', 'POSIX link graph; reparse support needs native type handling.')
    def test_framework_directory_and_multihop_file_links_have_complete_identity(self):
        framework = self.root / 'Product.app/Contents/Frameworks/Example.framework'
        version = framework / 'Versions/A'
        version.mkdir(parents=True)
        (version / 'Example').write_bytes(b'binary')
        (version / 'Resources').mkdir()
        (framework / 'Versions/Current').symlink_to('A', target_is_directory=True)
        (framework / 'Example').symlink_to('Versions/Current/Example')
        (framework / 'Resources').symlink_to('Versions/Current/Resources', target_is_directory=True)
        before = input_fingerprint(self.root)
        self.assertEqual(input_fingerprint(self.root), before)
        (version / 'Example').write_bytes(b'changed')
        self.assertNotEqual(input_fingerprint(self.root), before)

    @unittest.skipIf(os.name == 'nt', 'POSIX link graph.')
    def test_link_text_and_type_are_distinct_from_same_content_regular_file(self):
        (self.root / 'target').write_bytes(b'input')
        link = self.root / 'alias'
        link.symlink_to('target')
        first = input_fingerprint(self.root)
        link.unlink()
        link.symlink_to('./target')
        second = input_fingerprint(self.root)
        self.assertNotEqual(second, first)
        link.unlink()
        link.write_bytes(b'input')
        link.chmod(0o777)
        self.assertNotEqual(input_fingerprint(self.root), second)

    @unittest.skipIf(os.name == 'nt', 'POSIX link graph.')
    def test_internal_missing_and_cyclic_links_are_explicit_stable_namespace(self):
        (self.root / 'missing').symlink_to('later')
        (self.root / 'a').symlink_to('b')
        (self.root / 'b').symlink_to('a')
        before = input_fingerprint(self.root)
        self.assertEqual(input_fingerprint(self.root), before)
        (self.root / 'later').write_bytes(b'now exists')
        self.assertNotEqual(input_fingerprint(self.root), before)

    @unittest.skipIf(os.name == 'nt', 'POSIX link graph.')
    def test_parent_alias_does_not_recurse_and_its_target_changes_are_bound(self):
        (self.root / 'folder').mkdir()
        (self.root / 'folder/parent').symlink_to('..', target_is_directory=True)
        before = input_fingerprint(self.root)
        (self.root / 'new-input').write_bytes(b'input')
        self.assertNotEqual(input_fingerprint(self.root), before)

    @unittest.skipIf(os.name == 'nt', 'POSIX link graph.')
    def test_external_link_never_reads_outside_the_project(self):
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external).resolve() / 'secret'
            outside.write_bytes(b'private content')
            (self.root / 'alias').symlink_to(outside)
            with patch('local_tools.open_regular_file', wraps=local_tools.open_regular_file) as reading:
                with self.assertRaisesRegex(ValueError, '项目|范围|链接') as denied:
                    input_fingerprint(self.root)
            self.assertIn('alias', str(denied.exception))
            self.assertFalse(any(Path(call.args[0]) == outside for call in reading.call_args_list))

    @unittest.skipIf(os.name == 'nt', 'POSIX link graph.')
    def test_link_to_protected_input_is_not_accepted_as_complete_coverage(self):
        (self.root / '.env').write_bytes(b'private configuration')
        (self.root / 'alias').symlink_to('.env')
        with self.assertRaisesRegex(ValueError, '保护|覆盖|链接'):
            input_fingerprint(self.root)

    @unittest.skipIf(os.name == 'nt', 'POSIX link graph.')
    def test_link_retargeted_during_file_read_invalidates_observation(self):
        (self.root / 'target').write_bytes(b'input')
        link = self.root / 'alias'
        link.symlink_to('target')
        native = local_tools.open_regular_file

        @contextmanager
        def mutate(*args, **kwargs):
            link.unlink()
            link.symlink_to('./target')
            with native(*args, **kwargs) as stream:
                yield stream

        with patch('local_tools.open_regular_file', mutate):
            with self.assertRaisesRegex(ValueError, '修改|变化'):
                input_fingerprint(self.root)

    def test_deep_directory_inventory_does_not_use_python_recursion(self):
        path = self.root
        for _ in range(160):
            path = path / 'd'
            path.mkdir()
        (path / 'input').write_bytes(b'input')
        code = '''
import sys
from nailong.core.verification import input_fingerprint
sys.setrecursionlimit(120)
try:
    first = input_fingerprint(sys.argv[1])
    assert input_fingerprint(sys.argv[1]) == first
finally:
    sys.setrecursionlimit(1000)
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.root)],
            capture_output=True, text=True, timeout=10, env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)})
        self.assertEqual(result.returncode, 0, result.stderr)
