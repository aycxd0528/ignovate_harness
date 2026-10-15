"""Durable project work items and explicit session handoff contracts."""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore


class WorkItemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        self.base = Path(self.temp.name) / 'private'
        self.sessions = ProjectSessionStore(self.root, base_dir=self.base)
        self.tasks = TaskStore(self.sessions)

    def link(self, thread, task):
        method = getattr(self.tasks, 'link_work_item', None)
        self.assertTrue(callable(method), 'Explicit handoff must be supported')
        return method(thread, task['task_id'], expected_revision=task['revision'])

    def item(self, task_id):
        method = getattr(self.tasks, 'work_item', None)
        self.assertTrue(callable(method), 'Stable work item lookup must be supported')
        return method(task_id)

    def legacy(self):
        """A literal v1 task, predating the relationship store."""
        task = {'schema_version': 1, 'task_id': 'legacy-id', 'thread_id': 'owner',
            'project_root': str(self.root.resolve()), 'objective': 'Preserve original requirements',
            'scope': ['.'], 'constraints': ['Keep compatibility'], 'revision': 3,
            'latest_request': 'Continue safely', 'request_history': ['Preserve original requirements', 'Continue safely'],
            'profile': 'chat', 'lifecycle': 'paused', 'phase': 'implement',
            'steps': [], 'acceptance': [], 'changed_paths': ['source.py'],
            'pending_verification': ['Check original effect'], 'evidence': [],
            'progress': 'Interrupted after edit', 'blockers': ['Inspect actual files'],
            'input_fingerprint': 'old-input'}
        path = self.tasks.directory / 'owner.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(task), encoding='utf-8')
        return task, path

    def test_legacy_resume_retains_original_authority_and_provenance(self):
        legacy, path = self.legacy()
        original_bytes = path.read_bytes()
        restored = self.tasks.snapshot('owner')
        self.assertEqual(restored.get('owner_thread_id'), 'owner')
        self.assertEqual(restored['task_id'], 'legacy-id')
        self.assertEqual(restored['constraints'], ['Keep compatibility'])
        self.assertEqual(restored['lifecycle'], 'paused')
        self.assertEqual(path.read_bytes(), original_bytes)
        linked = self.link('resume', restored)
        self.assertEqual(linked['thread_id'], 'resume')
        self.assertEqual(linked['owner_thread_id'], 'owner')
        self.assertEqual(linked['source_thread_id'], 'owner')
        self.assertEqual(linked['association']['kind'], 'linked')
        self.assertEqual(linked['association']['source_revision'], 3)
        self.assertEqual(linked['evidence'], legacy['evidence'])
        self.assertFalse((self.tasks.directory / 'resume.json').exists())
        restarted = TaskStore(self.sessions).snapshot('resume')
        self.assertEqual(restarted, linked)

    def test_linked_sessions_share_requirements_revision_and_acceptance(self):
        task = self.tasks.begin('owner', 'Original requirement')
        self.link('resume', task)
        self.tasks.begin('resume', 'Additional requirement')
        self.tasks.add_acceptance('resume', 'feature', 'Check behavior', kind='manual')
        owner = self.tasks.snapshot('owner')
        resume = self.tasks.snapshot('resume')
        self.assertEqual(owner['task_id'], resume['task_id'])
        self.assertEqual(owner['revision'], 3)
        self.assertEqual(owner['request_history'], ['Original requirement', 'Additional requirement'])
        self.assertEqual(owner['acceptance'], resume['acceptance'])
        self.assertEqual(resume['thread_id'], 'resume')
        self.assertEqual(owner['thread_id'], 'owner')
        self.assertEqual(self.item(task['task_id'])['associated_thread_ids'], ['owner', 'resume'])

    def test_new_task_on_linked_session_detaches_without_changing_original(self):
        task = self.tasks.begin('owner', 'Original')
        self.link('resume', task)
        replacement = self.tasks.begin('resume', 'Independent', new=True)
        self.assertNotEqual(replacement['task_id'], task['task_id'])
        self.assertEqual(replacement['owner_thread_id'], 'resume')
        self.assertEqual(self.tasks.snapshot('owner')['objective'], 'Original')
        self.assertEqual(self.item(task['task_id'])['associated_thread_ids'], ['owner'])

    def test_owner_new_task_does_not_retarget_original_link(self):
        task = self.tasks.begin('owner', 'Original')
        self.link('resume', task)
        replacement = self.tasks.begin('owner', 'Independent', new=True)
        self.assertNotEqual(replacement['task_id'], task['task_id'])
        self.tasks.amend('resume', constraints=['Still original'])
        self.assertEqual(self.tasks.snapshot('resume')['task_id'], task['task_id'])
        self.assertEqual(self.tasks.snapshot('owner')['constraints'], [])
        self.assertEqual(self.item(task['task_id'])['constraints'], ['Still original'])
        items = self.tasks.list_work_items()
        self.assertEqual({row['task_id'] for row in items}, {task['task_id'], replacement['task_id']})

    def test_stale_handoff_and_independent_target_are_rejected(self):
        source = self.tasks.begin('owner', 'Original')
        self.tasks.amend('owner', constraints=['New requirement'])
        with self.assertRaises(ValueError):
            self.link('resume', source)
        self.assertIsNone(self.tasks.snapshot('resume'))
        current = self.tasks.snapshot('owner')
        target = self.tasks.begin('resume', 'Independent')
        with self.assertRaises(ValueError):
            self.link('resume', current)
        self.assertEqual(self.tasks.snapshot('resume'), target)

    def test_handoff_is_idempotent_but_does_not_retarget_existing_link(self):
        first = self.tasks.begin('one', 'First')
        linked = self.link('resume', first)
        self.assertEqual(self.link('resume', first), linked)
        second = self.tasks.begin('two', 'Second')
        with self.assertRaises(ValueError):
            self.link('resume', second)
        self.assertEqual(self.tasks.snapshot('resume'), linked)

    def test_project_isolation_does_not_import_foreign_task_or_authority(self):
        task = self.tasks.begin('owner', 'Project one')
        other_root = Path(self.temp.name) / 'other'
        other_root.mkdir()
        other = TaskStore(ProjectSessionStore(other_root, base_dir=self.base))
        self.assertTrue(callable(getattr(other, 'link_work_item', None)))
        with self.assertRaises(ValueError):
            other.link_work_item('resume', task['task_id'], expected_revision=1)
        self.assertEqual(other.list_work_items(), [])
        self.assertIsNone(other.snapshot('resume'))

    def test_wait_is_shared_explicit_and_revision_checked(self):
        task = self.tasks.begin('owner', 'Original')
        self.link('resume', task)
        self.assertTrue(callable(getattr(self.tasks, 'set_wait', None)))
        waiting = self.tasks.set_wait('resume', reason='CI running', condition='New CI result', expected_revision=1)
        self.assertEqual(waiting['wait_condition'], {'reason': 'CI running', 'condition': 'New CI result'})
        self.assertEqual(self.tasks.snapshot('owner')['lifecycle'], 'blocked')
        self.assertEqual(waiting['revision'], 1)
        self.tasks.amend('owner', constraints=['New requirement'])
        with self.assertRaises(ValueError):
            self.tasks.clear_wait('resume', expected_revision=1)
        cleared = self.tasks.clear_wait('resume', expected_revision=2)
        self.assertNotIn('wait_condition', cleared)
        self.assertEqual(cleared['lifecycle'], 'active')

    def test_reset_progress_does_not_silently_resolve_explicit_wait(self):
        task = self.tasks.begin('owner', 'Original')
        self.assertTrue(callable(getattr(self.tasks, 'set_wait', None)))
        self.tasks.set_wait('owner', reason='External effect unknown', condition='User checks result', expected_revision=1)
        restored = self.tasks.reset_progress('owner')
        self.assertEqual(restored['lifecycle'], 'blocked')
        self.assertEqual(restored['wait_condition']['condition'], 'User checks result')

    def test_old_snapshot_cannot_write_into_rebound_session(self):
        old = self.tasks.begin('owner', 'Original')
        replacement = self.tasks.begin('owner', 'Replacement', new=True)
        with self.assertRaises(ValueError):
            self.tasks._write('owner', dict(old, progress='Late result'))
        self.assertEqual(self.tasks.snapshot('owner'), replacement)
        with self.tasks.bound_task('owner', old['task_id'], old['revision']) as stale:
            self.assertIsNone(stale)

    def test_old_alias_snapshot_cannot_overwrite_newer_shared_requirements(self):
        task = self.tasks.begin('owner', 'Original')
        old = self.link('resume', task)
        current = self.tasks.amend('owner', constraints=['New requirement'])
        with self.assertRaises(ValueError):
            self.tasks._write('resume', dict(old, progress='Late result'))
        self.assertEqual(self.tasks.snapshot('owner'), current)

    def test_changed_canonical_identity_rejects_old_alias_write(self):
        task = self.tasks.begin('owner', 'Original')
        linked = self.link('resume', task)
        path = self.tasks._path('owner')
        damaged = json.loads(path.read_text())
        damaged['task_id'] = 'unexpected-replacement'
        path.write_text(json.dumps(damaged), encoding='utf-8')
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.tasks._write('resume', linked)
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.tasks.snapshot('resume')

    def test_requirement_changes_and_reconcile_do_not_resolve_explicit_wait(self):
        task = self.tasks.begin('owner', 'Original')
        self.tasks.set_wait('owner', reason='Review pending', condition='Reviewer decides', expected_revision=1)
        amended = self.tasks.amend('owner', constraints=['Additional constraint'])
        self.assertEqual(amended['lifecycle'], 'blocked')
        reconciled = self.tasks.reconcile('owner', current_input_fingerprint='changed')
        self.assertEqual(reconciled['lifecycle'], 'blocked')
        self.assertEqual(reconciled['wait_condition']['condition'], 'Reviewer decides')

    def test_interrupted_new_task_does_not_rebind_or_hide_original_work_item(self):
        task = self.tasks.begin('owner', 'Original')
        linked = self.link('resume', task)
        with patch.object(self.tasks.relationships, 'write', side_effect=OSError('Interrupted index write')):
            with self.assertRaises(OSError):
                self.tasks.begin('resume', 'Uncommitted replacement', new=True)
        self.assertEqual(TaskStore(self.sessions).snapshot('resume'), linked)
        try:
            items = self.tasks.list_work_items()
        except ValueError as error:
            self.fail(f'Uncommitted replacement hid the committed original: {error}')
        self.assertEqual([row['task_id'] for row in items], [task['task_id']])

    def test_valid_evidence_does_not_implicitly_complete_an_unresolved_wait(self):
        self.tasks.begin('owner', 'Original')
        self.tasks.add_acceptance('owner', 'manual', 'User checks result', kind='manual')
        self.tasks.user_decision('owner', 'manual', 'Observed result', current_input_fingerprint='current')
        self.tasks.set_wait('owner', reason='Delivery pending', condition='User releases delivery', expected_revision=2)
        before = self.tasks.snapshot('owner')
        with self.assertRaises(ValueError):
            self.tasks.set_state('owner', lifecycle='completed', current_input_fingerprint='current')
        self.assertEqual(self.tasks.snapshot('owner'), before)

    def test_corrupt_binding_shape_is_rejected_without_repairing_index(self):
        self.tasks.begin('owner', 'Original')
        path = self.tasks.relationships.path
        damaged = json.loads(path.read_text())
        damaged['bindings']['owner']['kind'] = []
        path.write_text(json.dumps(damaged), encoding='utf-8')
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            TaskStore(self.sessions).snapshot('owner')
        self.assertEqual(path.read_bytes(), before)

    def test_list_is_read_only_for_legacy_and_does_not_clone_to_empty_session(self):
        legacy, path = self.legacy()
        self.assertTrue(callable(getattr(self.tasks, 'list_work_items', None)))
        before = path.read_bytes()
        items = self.tasks.list_work_items()
        self.assertEqual([row['task_id'] for row in items], ['legacy-id'])
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.tasks.directory / 'work-items' / 'index.json').exists())
        self.assertIsNone(self.tasks.snapshot('unrelated'))
        self.assertEqual(self.tasks.begin('unrelated', 'Independent')['objective'], 'Independent')
        self.assertNotEqual(self.tasks.snapshot('unrelated')['task_id'], legacy['task_id'])

    def test_aliases_serialize_requirement_writes_across_processes(self):
        task = self.tasks.begin('owner', 'Original')
        self.link('resume', task)
        ready, done = Path(self.temp.name) / 'ready', Path(self.temp.name) / 'done'
        code = '''import sys
from pathlib import Path
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
tasks = TaskStore(ProjectSessionStore(sys.argv[1], base_dir=sys.argv[2]))
Path(sys.argv[3]).write_text('ready')
tasks.amend('resume', constraints=['Other process'])
Path(sys.argv[4]).write_text('done')
'''
        process = None
        try:
            with self.tasks.bound_task('owner', task['task_id'], task['revision']) as bound:
                self.assertIsNotNone(bound)
                process = subprocess.Popen([sys.executable, '-B', '-c', code, str(self.root), str(self.base), str(ready), str(done)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                deadline = time.monotonic() + 4
                while not ready.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail(process.communicate()[1])
                    time.sleep(.005)
                self.assertTrue(ready.exists())
                self.assertFalse(done.exists())
                self.tasks.set_state('resume', progress='Original process')
            _, error = process.communicate(timeout=4)
            self.assertEqual(process.returncode, 0, error)
            current = self.tasks.snapshot('owner')
            self.assertEqual(current['constraints'], ['Other process'])
            self.assertEqual(current['progress'], 'Original process')
            with self.tasks.bound_task('resume', task['task_id'], task['revision']) as stale:
                self.assertIsNone(stale)
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=4)
