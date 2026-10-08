"""Safe storage contracts shared by POSIX and the real Windows backend."""
import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class NativeFileBackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def backend(self):
        self.assertIsNotNone(importlib.util.find_spec('nailong.core.safe_files'),
                             'safe regular-file backend is missing')
        return importlib.import_module('nailong.core.safe_files')

    def test_regular_unicode_file_has_binary_and_text_access(self):
        backend = self.backend()
        path = self.root / '目录 with spaces' / 'hello.txt'
        path.parent.mkdir()
        path.write_bytes('你好\n'.encode())
        with backend.open_regular_file(path) as stream:
            self.assertEqual(stream.read(), '你好\n'.encode())
        with backend.open_regular_file(path, binary=False) as stream:
            self.assertEqual(stream.read(), '你好\n')

    def test_windows_path_syntax_rejects_streams_devices_and_ambiguous_names(self):
        backend = self.backend()
        for value in (r'C:\work\file:secret', r'\\.\NUL', r'\\?\C:\work\file',
                      r'C:\work\NUL.txt', r'C:\work\COM1', r'C:\work\LPT².txt',
                      r'C:\work\file.', r'C:\work\file ', r'C:relative',
                      r'C:\work\..\outside', r'\\server\share\file'):
            with self.subTest(path=value), self.assertRaises(ValueError):
                backend.validate_windows_path(value)
        backend.validate_windows_path(r'C:\work\目录 with spaces\file.md')

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink/fifo contract; Windows junction tests below')
    def test_regular_open_rejects_symlink_leaf_ancestor_and_fifo(self):
        backend = self.backend()
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'file').write_bytes(b'outside')
        link = self.root / 'link'
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            with backend.open_regular_file(link / 'file'): pass
        leaf = self.root / 'leaf'
        leaf.symlink_to(outside / 'file')
        with self.assertRaises((ValueError, OSError)):
            with backend.open_regular_file(leaf): pass
        pipe = self.root / 'pipe'
        os.mkfifo(pipe)
        with self.assertRaises((ValueError, OSError)):
            with backend.open_regular_file(pipe): pass

    @unittest.skipIf(os.name == 'nt', 'Windows named pipes cannot use ordinary drive paths')
    def test_lock_rejects_a_fifo_instead_of_treating_it_as_state(self):
        from nailong.core.file_locks import file_lock
        pipe = self.root / 'lock'
        os.mkfifo(pipe)
        with self.assertRaises((ValueError, OSError)):
            with file_lock(pipe, blocking=False): pass

    def test_atomic_write_publishes_complete_private_file_without_overwriting_immutable_record(self):
        backend = self.backend()
        path = self.root / 'new' / 'state.json'
        backend.atomic_write_bytes(path, b'first', replace=False)
        with self.assertRaises(FileExistsError):
            backend.atomic_write_bytes(path, b'second', replace=False)
        self.assertEqual(path.read_bytes(), b'first')
        backend.atomic_write_bytes(path, b'updated')
        self.assertEqual(path.read_bytes(), b'updated')
        if os.name != 'nt':
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual([item.name for item in path.parent.iterdir()], ['state.json'])

    @unittest.skipIf(os.name == 'nt', 'Windows ACL preservation contract below')
    def test_project_write_preserves_existing_permissions(self):
        backend = self.backend()
        path = self.root / 'shared.txt'
        path.write_bytes(b'original')
        path.chmod(0o640)
        backend.atomic_write_bytes(path, b'updated', private=False)
        self.assertEqual(path.read_bytes(), b'updated')
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_nonblocking_lease_conflicts_across_processes_and_releases(self):
        self.assertIsNotNone(importlib.util.find_spec('nailong.core.file_locks'),
                             'cross-platform file lock backend is missing')
        from nailong.core.file_locks import file_lock
        path = self.root / 'lease.lock'
        child = """import sys
from nailong.core.file_locks import file_lock
try:
    with file_lock(sys.argv[1], blocking=False):
        print('acquired')
except BlockingIOError:
    print('busy')
"""
        def attempt():
            result = subprocess.run([sys.executable, '-c', child, str(path)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()
        with file_lock(path):
            self.assertEqual(attempt(), 'busy')
        self.assertEqual(attempt(), 'acquired')

    @unittest.skipIf(os.name == 'nt', 'Windows junction equivalent below')
    def test_goal_store_rejects_symlink_state_before_resolving_it(self):
        from nailong.core.goal import GoalStore
        outside = self.root / 'outside.json'
        outside.write_bytes(b'[]')
        link = self.root / 'goals.json'
        link.symlink_to(outside)
        with self.assertRaises(ValueError):
            GoalStore(link).create('private goal')
        self.assertEqual(outside.read_bytes(), b'[]')

    def test_goal_and_task_transactions_keep_updates_from_separate_processes(self):
        from nailong.core.goal import GoalStore
        from nailong.core.sessions import ProjectSessionStore
        from nailong.core.task_state import TaskStore
        goal_path = self.root / 'goals.json'
        goal = GoalStore(goal_path).create('work', max_rounds=100, max_cost_usd=100)
        sessions = ProjectSessionStore(self.root, base_dir=self.root / 'data')
        tasks = TaskStore(sessions)
        tasks.begin('one', 'work')
        child = """import sys, time
from pathlib import Path
from nailong.core.goal import GoalStore
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
root = Path(sys.argv[1]); identity = sys.argv[3]
goals = GoalStore(root / 'goals.json')
tasks = TaskStore(ProjectSessionStore(root, base_dir=root / 'data'))
for index in range(8):
    goals.record_round(sys.argv[2], cost_usd=.01, files_changed=True, tool_calls=1,
                       round_id=identity + str(index))
    tasks.add_acceptance('one', identity + str(index), 'required ' + identity, kind='manual')
"""
        children = [subprocess.Popen([sys.executable, '-c', child, str(self.root), goal.id, identity],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for identity in ('first-', 'second-')]
        for process in children:
            _, error = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, error)
        settled = GoalStore(goal_path).get(goal.id)
        self.assertEqual(settled.round, 16)
        self.assertAlmostEqual(settled.spent_usd, .16)
        self.assertEqual(len(set(settled.settled_round_ids)), 16)
        state = tasks.snapshot('one')
        self.assertEqual(state['revision'], 17)
        self.assertEqual(len(state['acceptance']), 16)


@unittest.skipUnless(os.name == 'nt', 'requires actual Win32 handles and NTFS junctions')
class WindowsHandleTests(NativeFileBackendTests):
    def dacl(self, target):
        import ctypes
        from ctypes import wintypes
        from nailong.core._win32_files import LocalFree
        security = ctypes.WinDLL('advapi32', use_last_error=True)
        get_security = security.GetNamedSecurityInfoW
        get_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID,
            ctypes.POINTER(wintypes.LPVOID)]
        get_security.restype = wintypes.DWORD
        to_string = security.ConvertSecurityDescriptorToStringSecurityDescriptorW
        to_string.argtypes = [wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                             ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
        to_string.restype = wintypes.BOOL
        descriptor, output = wintypes.LPVOID(), wintypes.LPWSTR()
        self.assertEqual(get_security(str(target), 1, 4, None, None, None, None,
                                      ctypes.byref(descriptor)), 0)
        try:
            self.assertTrue(to_string(descriptor, 1, 4, ctypes.byref(output), None))
            return output.value
        finally:
            if output: LocalFree(ctypes.cast(output, wintypes.LPVOID))
            LocalFree(descriptor)

    def junction(self, link, target):
        result = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.addCleanup(lambda: link.rmdir() if link.exists() else None)

    def test_read_and_atomic_write_reject_junction_ancestors(self):
        backend = self.backend()
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'file').write_bytes(b'outside')
        link = self.root / 'link'
        self.junction(link, outside)
        self.assertTrue(backend.is_link_or_reparse(link))
        with self.assertRaises((OSError, ValueError)):
            with backend.open_regular_file(link / 'file'): pass
        with self.assertRaises((OSError, ValueError)):
            backend.atomic_write_bytes(link / 'new', b'private')
        self.assertFalse((outside / 'new').exists())

    def test_open_stream_pins_each_parent_against_rename(self):
        backend = self.backend()
        parent = self.root / 'parent' / 'nested'
        parent.mkdir(parents=True)
        path = parent / 'file'
        path.write_bytes(b'inside')
        with backend.open_regular_file(path) as stream:
            for directory in (parent, parent.parent):
                with self.subTest(directory=directory), self.assertRaises(OSError):
                    directory.rename(directory.with_name(directory.name + '-moved'))
            self.assertEqual(stream.read(), b'inside')
        parent.rename(parent.with_name('renamed'))

    def test_pinned_parent_denies_handles_that_can_install_a_reparse_point(self):
        import ctypes
        from nailong.core._win32_files import (CreateFile, CloseHandle, GENERIC_WRITE,
            OPEN_EXISTING, OPEN_REPARSE_POINT, BACKUP_SEMANTICS, INVALID_HANDLE_VALUE, extended)
        backend = self.backend()
        parent = self.root / 'parent'
        parent.mkdir()
        path = parent / 'file'
        path.write_bytes(b'private')
        with backend.open_regular_file(path):
            handle = CreateFile(extended(parent), GENERIC_WRITE, 7, None,
                                OPEN_EXISTING, OPEN_REPARSE_POINT | BACKUP_SEMANTICS, None)
            if handle != INVALID_HANDLE_VALUE:
                CloseHandle(handle)
            self.assertEqual(handle, INVALID_HANDLE_VALUE)
            self.assertEqual(ctypes.get_last_error(), 32)  # ERROR_SHARING_VIOLATION

    def test_alternate_stream_is_rejected_before_access(self):
        backend = self.backend()
        path = self.root / 'file'
        path.write_bytes(b'ordinary')
        stream = str(path) + ':hidden'
        with open(stream, 'wb') as output: output.write(b'hidden')
        with self.assertRaises(ValueError):
            with backend.open_regular_file(stream): pass
        with self.assertRaises(ValueError):
            backend.atomic_write_bytes(stream, b'changed')
        self.assertEqual(path.read_bytes(), b'ordinary')

    def test_archive_goal_and_memory_reject_junction_state_directories(self):
        from langchain_core.messages import HumanMessage
        from nailong.core.history_archive import HistoryArchive
        from nailong.core.goal import GoalStore
        from nailong.core.memory import MemoryStore
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.root / 'link'
        self.junction(link, outside)
        with self.assertRaises((OSError, ValueError)):
            HistoryArchive(link / 'archive').save('one', HumanMessage(content='private'))
        with self.assertRaises((OSError, ValueError)):
            GoalStore(link / 'goals.json').create('private')
        project = self.root / 'project'
        project.mkdir()
        self.junction(project / '.nailong', outside)
        with self.assertRaises((OSError, ValueError)):
            MemoryStore(project).commit(MemoryStore(project).stage('project', 'private'))
        self.assertEqual(list(outside.iterdir()), [])

    def test_archive_replays_the_original_private_record(self):
        from langchain_core.messages import HumanMessage
        from nailong.core.history_archive import HistoryArchive
        archive = HistoryArchive(self.root / 'archive', api_key='secret')
        reference = archive.save('one', HumanMessage(content='old secret'))
        self.assertEqual(archive.read('one', reference)['content'], 'old [密钥已隐藏]')
        self.assertEqual(archive.save('one', HumanMessage(content='old secret')), reference)
        self.assertEqual(len(list((self.root / 'archive').rglob('*.json'))), 1)

    def test_atomic_publication_pins_parents_until_rename_finishes(self):
        backend = self.backend()
        directory = self.root / 'state'
        directory.mkdir()
        real_replace = os.replace
        attempts = []
        def replace(source, destination, *args, **kwargs):
            with self.assertRaises(OSError):
                directory.rename(self.root / 'moved')
            attempts.append(True)
            return real_replace(source, destination, *args, **kwargs)
        with patch('nailong.core.safe_files.os.replace', side_effect=replace):
            backend.atomic_write_bytes(directory / 'file', b'private')
        self.assertEqual(attempts, [True])
        self.assertEqual((directory / 'file').read_bytes(), b'private')
        directory.rename(self.root / 'moved')

    def test_private_storage_has_protected_user_system_admin_dacl(self):
        from nailong.core._win32_files import _user_sid
        backend = self.backend()
        path = self.root / 'private' / 'file'
        backend.atomic_write_bytes(path, b'private')
        for target in (path, path.parent):
            with self.subTest(target=target):
                value = self.dacl(target)
                self.assertTrue(value.startswith('D:P'), value)
                trustees = [ace.split(';')[-1] for ace in value.split('(')[1:]]
                self.assertEqual({sid.rstrip(')') for sid in trustees}, {_user_sid(), 'SY', 'BA'})
        backend.private_file_permissions(path)
        backend.private_directory_permissions(path.parent)
        self.assertTrue(self.dacl(path).startswith('D:P'))

    def test_project_replacement_preserves_existing_dacl_and_new_file_inherits_parent(self):
        backend = self.backend()
        path = self.root / 'shared.txt'
        path.write_bytes(b'original')
        result = subprocess.run(['icacls', str(path), '/grant', '*S-1-1-0:(R)'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        original = self.dacl(path)
        backend.atomic_write_bytes(path, b'updated', private=False)
        self.assertEqual(path.read_bytes(), b'updated')
        self.assertEqual(self.dacl(path), original)
        new = self.root / 'new.txt'
        backend.atomic_write_bytes(new, b'new', private=False)
        self.assertFalse(self.dacl(new).startswith('D:P'), self.dacl(new))

    def test_project_write_keeps_readonly_target_and_original_data_on_failure(self):
        backend = self.backend()
        path = self.root / 'readonly.txt'
        path.write_bytes(b'original')
        path.chmod(0o444)
        self.addCleanup(lambda: path.chmod(0o666))
        original = self.dacl(path)
        with self.assertRaises(OSError):
            backend.atomic_write_bytes(path, b'updated', private=False)
        self.assertEqual(path.read_bytes(), b'original')
        self.assertTrue(path.stat().st_file_attributes & 1)
        self.assertEqual(self.dacl(path), original)

    def test_lock_pins_ancestors_while_lease_is_held(self):
        from nailong.core.file_locks import file_lock
        parent = self.root / 'leases'
        with file_lock(parent / 'driver.lock'):
            with self.assertRaises(OSError):
                parent.rename(self.root / 'moved')
        parent.rename(self.root / 'moved')
