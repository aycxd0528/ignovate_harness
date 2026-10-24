"""Complete input identities must scale without omitting later mutations."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import local_tools
from nailong.core.verification import input_fingerprint


class InputFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()

    def test_more_than_5000_inputs_remain_covered_including_the_last_file(self):
        for index in range(5001):
            (self.root / f'{index:05}.txt').write_bytes(b'input')
        before = input_fingerprint(self.root)
        (self.root / '05000.txt').write_bytes(b'other')
        self.assertNotEqual(input_fingerprint(self.root), before)

    def test_large_input_is_fully_hashed_including_its_last_byte(self):
        source = self.root / 'large.bin'
        with source.open('wb') as stream:
            stream.truncate(51 * 1024 * 1024)
        before = input_fingerprint(self.root)
        with source.open('r+b') as stream:
            stream.seek(-1, os.SEEK_END)
            stream.write(b'x')
        self.assertNotEqual(input_fingerprint(self.root), before)

    @unittest.skipIf(os.name == 'nt', 'Windows creation time is not a content cache version')
    def test_unchanged_input_reuses_digest_but_same_size_restored_mtime_does_not(self):
        source = self.root / 'input.txt'
        source.write_bytes(b'before')
        reads = []
        original = local_tools.open_regular_file

        @contextmanager
        def observe(path, *args, **kwargs):
            reads.append(Path(path).name)
            with original(path, *args, **kwargs) as stream:
                yield stream

        import time
        with patch('local_tools.open_regular_file', observe), patch(
                'nailong.core.input_snapshot.time.time_ns', return_value=time.time_ns()+3_000_000_000):
            before = input_fingerprint(self.root)
            self.assertEqual(input_fingerprint(self.root), before)
            self.assertEqual(reads, ['input.txt'])
            version = source.stat()
            source.write_bytes(b'after!')
            os.utime(source, ns=(version.st_atime_ns, version.st_mtime_ns))
            self.assertNotEqual(input_fingerprint(self.root), before)
            self.assertEqual(reads, ['input.txt', 'input.txt'])

    def test_changes_to_an_earlier_file_during_scan_never_certify_a_mixed_snapshot(self):
        (self.root / 'a.txt').write_bytes(b'before')
        (self.root / 'z.txt').write_bytes(b'trigger')
        original = local_tools.open_regular_file

        @contextmanager
        def mutate(path, *args, **kwargs):
            if Path(path).name == 'z.txt':
                (self.root / 'a.txt').write_bytes(b'after!')
            with original(path, *args, **kwargs) as stream:
                yield stream

        with patch('local_tools.open_regular_file', mutate):
            with self.assertRaisesRegex(ValueError, '修改|变化'):
                input_fingerprint(self.root)

    def test_new_input_added_during_scan_is_not_silently_omitted(self):
        (self.root / 'a.txt').write_bytes(b'input')
        original = local_tools.open_regular_file

        @contextmanager
        def mutate(path, *args, **kwargs):
            (self.root / 'new.txt').write_bytes(b'late input')
            with original(path, *args, **kwargs) as stream:
                yield stream

        with patch('local_tools.open_regular_file', mutate):
            with self.assertRaisesRegex(ValueError, '修改|变化'):
                input_fingerprint(self.root)

    def test_cached_input_removed_replaced_or_chmodded_changes_identity(self):
        source = self.root / 'input.txt'
        source.write_bytes(b'before')
        before = input_fingerprint(self.root)
        replacement = self.root / 'replacement.tmp'
        replacement.write_bytes(b'after!')
        os.replace(replacement, source)
        replaced = input_fingerprint(self.root)
        self.assertNotEqual(replaced, before)
        if os.name != 'nt':
            source.chmod(0o700)
            self.assertNotEqual(input_fingerprint(self.root), replaced)
        source.unlink()
        self.assertNotEqual(input_fingerprint(self.root), replaced)

    def test_cached_input_replaced_by_external_symlink_is_rejected(self):
        if os.name == 'nt':
            self.skipTest('Windows symlink privileges are exercised in native file tests')
        source = self.root / 'input.txt'
        source.write_bytes(b'input')
        input_fingerprint(self.root)
        source.unlink()
        source.symlink_to(self.root.parent / 'outside')
        with self.assertRaises(ValueError):
            input_fingerprint(self.root)

    def test_deadline_never_returns_an_incomplete_fingerprint(self):
        (self.root / 'input.txt').write_bytes(b'input')
        with patch('nailong.core.input_snapshot.MAX_SNAPSHOT_SECONDS', 0):
            with self.assertRaisesRegex(ValueError, '超时'):
                input_fingerprint(self.root)
        self.assertTrue(input_fingerprint(self.root))

    def test_digest_cache_eviction_never_omits_input(self):
        from collections import OrderedDict
        import time
        import nailong.core.input_snapshot as snapshots
        for name in ('a.txt', 'b.txt', 'c.txt'):
            (self.root / name).write_bytes(b'before')
        cache = OrderedDict()
        with patch.object(snapshots, '_cache', cache), patch.object(snapshots, '_cache_bytes', 0), patch.object(snapshots, 'MAX_CACHED_FILES', 2), patch(
                'nailong.core.input_snapshot.time.time_ns', return_value=time.time_ns()+3_000_000_000):
            before = input_fingerprint(self.root)
            self.assertLessEqual(len(cache), 2)
            (self.root / 'a.txt').write_bytes(b'after!')
            self.assertNotEqual(input_fingerprint(self.root), before)
            self.assertLessEqual(len(cache), 2)

    def test_disk_inventory_exhaustion_never_returns_partial_proof_and_cleans_up(self):
        import nailong.core.snapshot_inventory as inventories
        (self.root / 'input.txt').write_bytes(b'input')
        import sqlite3
        import nailong.core.ephemeral_sqlite as scratch
        original=sqlite3.connect;created=[]
        def observe(*args,**kwargs):
            connection=original(*args,**kwargs);created.append(connection);return connection
        with patch.object(inventories,'MAX_INDEX_PAGES',1),patch.object(scratch.sqlite3,'connect',side_effect=observe):
            with self.assertRaisesRegex(ValueError,'临时索引'):input_fingerprint(self.root)
        self.assertTrue(created)
        for connection in created:
            with self.assertRaises(sqlite3.ProgrammingError):connection.execute('SELECT 1')
        self.assertTrue(input_fingerprint(self.root))

    def test_versioned_directory_and_file_order_is_stable(self):
        import hashlib
        import stat
        paths = ['z.txt', 'a/b.txt', 'a/deep/c.txt', 'a-b/d.txt', 'aa/e.txt', '中/f.txt']
        expected = hashlib.sha256(b'ignovate-input-v2\0')
        for relative in paths:
            path = self.root / relative
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(b'input')
        directories = {Path()}
        for relative in paths:
            directories.update(Path(relative).parents)
        def entry(kind, relative, payload=b''):
            for item in (kind, os.fsencode(relative) if relative.parts else b'',
                         str(stat.S_IMODE((self.root / relative).stat().st_mode)).encode(), payload):
                expected.update(len(item).to_bytes(8, 'big'))
                expected.update(item)
        for relative in sorted(directories, key=lambda path: os.fsencode(path) if path.parts else b''):
            entry(b'directory', relative)
        for relative in paths:
            entry(b'file', Path(relative), hashlib.sha256(b'input').digest())
        expected.update(bytes(16))  # Two length-framed empty Git fields.
        self.assertEqual(input_fingerprint(self.root), expected.hexdigest())

    def test_recent_file_is_reread_across_a_coarse_timestamp_tick(self):
        (self.root / 'input.txt').write_bytes(b'input')
        original = local_tools.open_regular_file
        with patch('local_tools.open_regular_file', wraps=original) as reads:
            before = input_fingerprint(self.root)
            self.assertEqual(input_fingerprint(self.root), before)
            self.assertEqual(reads.call_count, 2)

    @unittest.skipIf(os.name == 'nt', 'Windows does not reuse content digests')
    def test_fresh_coarse_timestamp_digest_cannot_become_a_stale_cache_hit_later(self):
        import time
        import nailong.core.input_snapshot as snapshots
        source = self.root / 'input.txt'
        source.write_bytes(b'before')
        initial = source.stat()
        tick = time.time_ns() // 1_000_000_000 * 1_000_000_000
        original = snapshots._version
        with patch.object(snapshots, '_version', side_effect=lambda value: (*original(value)[:-1], tick)), patch(
                'nailong.core.input_snapshot.time.time_ns', side_effect=[tick+100_000_000, tick+3_000_000_000]):
            before = input_fingerprint(self.root)
            source.write_bytes(b'after!')
            os.utime(source, ns=(initial.st_atime_ns, initial.st_mtime_ns))
            self.assertNotEqual(input_fingerprint(self.root), before)


class AsyncInputFingerprintTests(unittest.IsolatedAsyncioTestCase):
    setUp = InputFingerprintTests.setUp

    async def test_repeated_cancellation_stops_scan_after_the_pending_read_and_joins_it(self):
        import asyncio
        import threading
        from nailong.core.verification import _owned_worker
        source = self.root / 'large.bin'
        with source.open('wb') as stream:
            stream.truncate(8 * 1024 * 1024)
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        reads = []
        original = local_tools.open_regular_file

        @contextmanager
        def pause_read(path, *args, **kwargs):
            with original(path, *args, **kwargs) as stream:
                class Reader:
                    def fileno(self):
                        return stream.fileno()

                    def read(self, size):
                        reads.append(size)
                        if len(reads) == 1:
                            entered.set()
                            if not release.wait(3):
                                raise RuntimeError('test reader was not released')
                        return stream.read(size)
                try:
                    yield Reader()
                finally:
                    closed.set()

        with patch('local_tools.open_regular_file', pause_read):
            task = asyncio.create_task(_owned_worker(input_fingerprint, self.root))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(closed.is_set())
        self.assertEqual(len(reads), 1, 'cancelled snapshots must stop before reading another block')


class LargeInputVerificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        self.root = Path(self.directory.name).resolve()

    async def test_native_large_project_verification_invalidates_when_a_late_input_changes(self):
        import json
        from platform_fixtures import python_command
        from nailong.core.goal import GoalStore
        from nailong.core.permissions import PermissionEngine
        from nailong.core.sessions import ProjectSessionStore
        from nailong.core.task_state import TaskStore
        from nailong.core.verification import VerificationService

        inputs = self.root / 'inputs'
        inputs.mkdir()
        for index in range(5001):
            (inputs / f'{index:05}.txt').write_bytes(b'source')
        with (self.root / 'large.bin').open('wb') as stream:
            stream.truncate(51 * 1024 * 1024)
        settings = self.root / '.nailong'
        settings.mkdir()
        (settings / 'settings.json').write_text(json.dumps({'verification': {'steps': [{
            'name': 'all-inputs', 'kind': 'test',
            'command': python_command("from pathlib import Path; assert all(p.read_bytes()==b'source' for p in Path('inputs').iterdir())"),
            'timeout_seconds': 30}]}}))
        sessions = ProjectSessionStore(self.root, base_dir=self.data.name)
        tasks = TaskStore(sessions)
        goals = GoalStore(Path(self.data.name) / 'goals.json', project_root=self.root)
        goals.task_store = tasks
        tasks.begin('thread', '验证全部输入')
        tasks.add_acceptance('thread', 'inputs', '全部输入保持正确', kind='test')
        verifier = VerificationService(self.root, sessions, goals, task_store=tasks)
        tasks.configure_verification('thread', verifier.list_steps())
        tasks.bind_verification('thread', 'inputs', 'all-inputs', ['.'])
        goal = goals.create('验证全部输入', thread_id='thread')

        result = await verifier.run('thread', permission_engine=PermissionEngine(self.root),
                                    permission_mode='bypassPermissions')

        self.assertEqual(result['status'], 'passed')
        self.assertTrue(result['steps'][0]['started'])
        self.assertTrue(goals.get(goal.id).verification_succeeded)
        (inputs / '05000.txt').write_bytes(b'broken')
        accepted, _, current = goals.update(goal.id, state='complete', thread_id='thread')
        self.assertFalse(accepted)
        self.assertFalse(current.verification_succeeded)
        self.assertEqual(current.state, 'active')
