"""Slow real input observations must not freeze the GUI or certify changed tasks."""
import asyncio
import http.client
import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import test_gui_controller as controller_fixture
import test_gui_server as server_fixture
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
from nailong.core.verification import input_fingerprint


class PausedFingerprint:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.threads = []

    def __call__(self, root, generated=()):
        value = input_fingerprint(root, generated)
        self.threads.append(threading.get_ident())
        self.started.set()
        # A deadline also lets the regression fail cleanly when the event loop
        # itself is blocked and cannot execute the test's finally clause.
        self.release.wait(2)
        return value


class SnapshotHTTPScanTests(unittest.TestCase):
    def setUp(self):
        from nailong.gui.controller import GUIController
        from nailong.gui.server import GUIServer
        self.temp = tempfile.TemporaryDirectory()
        environment = patch.dict(os.environ)
        environment.start(); self.addCleanup(environment.stop)
        project = Path(self.temp.name)/'project'; project.mkdir()
        self.server = GUIServer(project, port=0, token='test-token', controller_factory=lambda root:
            GUIController(root, sessions=ProjectSessionStore(root, base_dir=Path(self.temp.name)/'data')))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)

    tearDown = server_fixture.ServerTests.tearDown
    request = server_fixture.ServerTests.request

    def test_http_snapshot_scan_leaves_events_endpoint_responsive(self):
        def prepare():
            controller = self.server.controller
            branch = controller.branches.create('manual check')
            tasks = TaskStore(controller.sessions)
            tasks.begin(branch['thread_id'], 'inspect project')
            tasks.add_acceptance(branch['thread_id'], 'human', 'inspect output', kind='manual')
            tasks.user_decision(branch['thread_id'], 'human', 'reviewed',
                                current_input_fingerprint=input_fingerprint(controller.project))
        self.server.call(prepare)
        gate = PausedFingerprint()
        result = []
        def snapshot_request():
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
            try:
                connection.request('GET', '/api/snapshot', headers={'Authorization': 'Bearer test-token'})
                response = connection.getresponse()
                result.append((response.status, json.loads(response.read())))
            finally:
                connection.close()
        with patch('nailong.core.verification.input_fingerprint', gate):
            request = threading.Thread(target=snapshot_request)
            request.start()
            try:
                self.assertTrue(gate.started.wait(2), 'snapshot never observed current project inputs')
                heartbeat = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=.3)
                try:
                    heartbeat.request('GET', '/api/events', headers={'Authorization': 'Bearer test-token'})
                    try:
                        response = heartbeat.getresponse()
                        response.read()
                    except TimeoutError:
                        self.fail('input scanning blocked the HTTP events heartbeat')
                    self.assertEqual(response.status, 200)
                finally:
                    heartbeat.close()
            finally:
                gate.release.set()
                request.join(4)
        self.assertEqual(result[0][0], 200)
        self.assertFalse(any(row.get('acceptance_id') == 'human' for row in result[0][1]['control']['attention']), result[0][1]['control'])
        self.assertNotIn(self.server.loop_thread.ident, gate.threads)


class CommandScanTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await controller_fixture.ControllerTests.asyncSetUp(self)
        self.tasks = TaskStore(self.sessions)
        self.controller.service.task_store = self.tasks
        self.thread_id = self.branch['thread_id']
        self.tasks.begin(self.thread_id, 'inspect project')
        self.tasks.add_acceptance(self.thread_id, 'human', 'inspect output', kind='manual')

    asyncTearDown = controller_fixture.ControllerTests.asyncTearDown
    until = controller_fixture.ControllerTests.until

    async def start_scan(self, name, payload, gate):
        pending = asyncio.create_task(self.controller.command(name, payload))
        self.addAsyncCleanup(self.finish_scan, pending, gate)
        self.assertTrue(await asyncio.to_thread(gate.started.wait, 3))
        self.assertFalse(pending.done(), 'input scanning ran on the GUI event loop')
        return pending

    async def finish_scan(self, pending, gate):
        gate.release.set()
        await asyncio.gather(pending, return_exceptions=True)

    def confirmation(self):
        return {'operation': 'confirm_acceptance', 'branch_id': self.branch['id'],
                'expected_revision': self.tasks.snapshot(self.thread_id)['revision'],
                'acceptance_id': 'human', 'reason': 'inspected current output'}

    async def test_confirmation_scan_releases_command_lock_and_rejects_replaced_task(self):
        gate = PausedFingerprint()
        with patch('nailong.core.verification.input_fingerprint', gate):
            pending = await self.start_scan('work_control', self.confirmation(), gate)
            # A real waiting run provides the stop command's normal authority.
            queued = await asyncio.wait_for(self.controller.command('send', {
                'branch_id': self.branch['id'], 'message': 'wait', 'request_id': 'during-scan'}), .3)
            await self.until(lambda: self.controller.runs[queued['run_id']]['status'] == 'running')
            stopped = await asyncio.wait_for(self.controller.command('stop', {'run_id': queued['run_id']}), .3)
            self.assertEqual(stopped['status'], 'stopped')
            replacement = self.tasks.begin(self.thread_id, 'unrelated new task', new=True)
            gate.release.set()
            with self.assertRaisesRegex(ValueError, '任务|版本|变化'):
                await pending
        current = self.tasks.snapshot(self.thread_id)
        self.assertEqual(current['task_id'], replacement['task_id'])
        self.assertFalse(current['evidence'])
        self.assertNotIn(threading.get_ident(), gate.threads)

    async def test_confirmation_rejects_same_revision_state_change_and_holds_no_task_lock(self):
        gate = PausedFingerprint()
        payload = self.confirmation()
        with patch('nailong.core.verification.input_fingerprint', gate):
            pending = await self.start_scan('work_control', payload, gate)
            external = TaskStore(self.sessions)
            changed = await asyncio.wait_for(asyncio.to_thread(external.set_wait, self.thread_id,
                reason='new dependency', condition='new result', expected_revision=payload['expected_revision']), .3)
            self.assertEqual(changed['revision'], payload['expected_revision'])
            gate.release.set()
            with self.assertRaisesRegex(ValueError, '任务|版本|变化'):
                await pending
        self.assertEqual(self.tasks.snapshot(self.thread_id)['acceptance'][0]['status'], 'pending')

    async def test_confirmation_scan_cannot_follow_a_project_switch(self):
        gate = PausedFingerprint()
        other = Path(self.temp.name) / 'other'; other.mkdir()
        with patch('nailong.core.verification.input_fingerprint', gate):
            pending = await self.start_scan('work_control', self.confirmation(), gate)
            with patch('nailong.gui.controller.ProjectSessionStore', side_effect=lambda root:
                       ProjectSessionStore(root, base_dir=Path(self.temp.name)/'data')):
                opened = await asyncio.wait_for(self.controller.command('open_project', {'path': str(other)}), .3)
            self.assertEqual(opened['project']['path'], str(other.resolve()))
            gate.release.set()
            with self.assertRaisesRegex(ValueError, '项目|变化'):
                await pending
        self.assertEqual(self.tasks.snapshot(self.thread_id)['acceptance'][0]['status'], 'pending')

    async def test_confirmation_records_the_observed_current_inputs(self):
        path = self.project/'source.py'; path.write_text('current input')
        expected = input_fingerprint(self.project)
        result = await self.controller.command('work_control', self.confirmation())
        self.assertEqual(result['input_fingerprint'], expected)
        self.assertEqual(result['acceptance'][0]['status'], 'passed')
        self.assertEqual(result['evidence'][-1]['input_fingerprint'], expected)

    async def test_status_scan_projects_changed_task_with_unknown_manual_evidence(self):
        self.tasks.user_decision(self.thread_id, 'human', 'reviewed',
                                 current_input_fingerprint=input_fingerprint(self.project))
        task = self.tasks.snapshot(self.thread_id)
        gate = PausedFingerprint()
        with patch('nailong.core.verification.input_fingerprint', gate):
            pending = await self.start_scan('work_control', {'operation': 'status'}, gate)
            self.tasks.set_wait(self.thread_id, reason='changed while scanning', condition='updated result',
                                expected_revision=task['revision'])
            gate.release.set()
            result = await pending
        manual = next(row for row in result['attention'] if row.get('acceptance_id') == 'human')
        self.assertEqual(manual['status'], 'unknown')
        self.assertTrue(any(row['kind'] == 'wait' for row in result['attention']))

    async def test_unknown_scan_never_confirms_acceptance_or_reports_current_manual_evidence(self):
        self.tasks.user_decision(self.thread_id, 'human', 'reviewed',
                                 current_input_fingerprint=input_fingerprint(self.project))
        before = self.tasks.snapshot(self.thread_id)
        with patch('nailong.core.verification.input_fingerprint', side_effect=ValueError('scan incomplete')):
            status = await self.controller.command('work_control', {'operation': 'status'})
            with self.assertRaisesRegex(ValueError, 'scan incomplete'):
                await self.controller.command('work_control', self.confirmation())
        self.assertEqual(next(row for row in status['attention'] if row.get('acceptance_id') == 'human')['status'], 'unknown')
        self.assertEqual(self.tasks.snapshot(self.thread_id), before)

    async def test_async_snapshot_keeps_task_diagnostics_when_authority_is_unreadable(self):
        # Inject only the authority read failure; snapshot and projection remain
        # real so this checks the GUI's existing read-only failure contract.
        with patch.object(self.tasks, 'list_work_items', side_effect=ValueError('task authority unreadable')):
            result = await self.controller.snapshot_async()
        self.assertEqual(result['control']['diagnostics'], ['task authority unreadable'])
        self.assertEqual(result['control']['items'], [])

    def verification_node(self):
        self.sessions.append_event(self.thread_id, 'verification', {'status': 'passed',
            'input_after': input_fingerprint(self.project), 'generated_paths': []})
        return self.controller.branches.commit_turn(self.branch['id'], 0, None,
                                                   task_snapshot=self.tasks.snapshot(self.thread_id))

    async def test_node_and_export_scan_allow_approval_and_detect_stale_inputs(self):
        node = self.verification_node()
        (self.project / 'source.py').write_text('changed after verification')
        for name, payload in [('node', {'node_id': node['id']}), ('export', {'branch_id': self.branch['id']})]:
            with self.subTest(command=name):
                gate = PausedFingerprint()
                future = asyncio.get_running_loop().create_future()
                self.controller.approvals['approval'] = {'future': future,
                    'public': {'run_id': 'waiting', 'branch_id': self.branch['id'], 'action': {'name': 'run_command'}}}
                with patch('nailong.core.verification.input_fingerprint', gate):
                    pending = await self.start_scan(name, payload, gate)
                    approved = await asyncio.wait_for(self.controller.command('approve', {
                        'action_id': 'approval', 'run_id': 'waiting', 'decision': 'approve_once'}), .3)
                    self.assertTrue(approved['accepted'])
                    gate.release.set()
                    result = await pending
                detail = result if name == 'node' else json.loads(result['text'])['path'][0]
                self.assertEqual(detail['delivery']['status'], 'stale')

    async def test_feedback_scan_releases_task_lock_and_rejects_changed_source_task(self):
        fingerprint = input_fingerprint(self.project)
        self.tasks.user_decision(self.thread_id, 'human', 'reviewed', current_input_fingerprint=fingerprint)
        task = self.tasks.snapshot(self.thread_id)
        self.sessions.append_event(self.thread_id, 'user', {'text': 'source'})
        node = self.controller.branches.commit_turn(self.branch['id'], 0, None, task_snapshot=task)
        payload = {'operation': 'feedback_stage', 'branch_id': self.branch['id'], 'node_id': node['id'],
            'expected_revision': task['revision'], 'context': 'context', 'outcome': 'outcome',
            'behavior': 'behavior', 'boundary': 'boundary', 'request_id': 'concurrent-feedback'}
        gate = PausedFingerprint()
        with patch('nailong.core.verification.input_fingerprint', gate):
            pending = await self.start_scan('work_control', payload, gate)
            changed = await asyncio.wait_for(asyncio.to_thread(TaskStore(self.sessions).set_wait, self.thread_id,
                reason='changed while scanning', condition='new result', expected_revision=task['revision']), .3)
            self.assertEqual(changed['revision'], task['revision'])
            gate.release.set()
            with self.assertRaisesRegex(ValueError, '任务|版本|变化'):
                await pending
        from nailong.core.feedback import FeedbackStore
        self.assertEqual(FeedbackStore(self.sessions).list(), [])

    def feedback_payload(self, request_id):
        self.sessions.append_event(self.thread_id, 'user', {'text': 'feedback source'})
        node = self.controller.branches.commit_turn(self.branch['id'], 0, None,
                                                   task_snapshot=self.tasks.snapshot(self.thread_id))
        return {'operation': 'feedback_stage', 'branch_id': self.branch['id'], 'node_id': node['id'],
            'expected_revision': self.tasks.snapshot(self.thread_id)['revision'], 'context': 'context',
            'outcome': 'outcome', 'behavior': 'behavior', 'boundary': 'boundary', 'request_id': request_id}

    async def test_native_feedback_stage_scan_without_known_source_leaves_commands_responsive(self):
        gate = PausedFingerprint()
        payload = self.feedback_payload('native-stage')
        with patch('nailong.core.verification.input_fingerprint', gate):
            pending = await self.start_scan('work_control', payload, gate)
            created = await asyncio.wait_for(self.controller.command('create', {'name': 'while scanning'}), .3)
            self.assertEqual(created['name'], 'while scanning')
            gate.release.set()
            row = await pending
        self.assertEqual(row['state'], 'candidate')
        self.assertNotIn(threading.get_ident(), gate.threads)

    async def test_native_feedback_confirm_scan_holds_no_task_lock_and_rejects_changed_task(self):
        from nailong.core.feedback import FeedbackStore
        row = await self.controller.command('work_control', self.feedback_payload('native-confirm-state'))
        gate = PausedFingerprint()
        with patch('nailong.core.verification.input_fingerprint', gate):
            pending = await self.start_scan('work_control', {'operation': 'feedback_confirm',
                'branch_id': self.branch['id'], 'id': row['id'], 'expected_revision': row['revision']}, gate)
            task = self.tasks.snapshot(self.thread_id)
            await asyncio.wait_for(asyncio.to_thread(TaskStore(self.sessions).set_wait, self.thread_id,
                reason='new input required', condition='updated result', expected_revision=task['revision']), .3)
            gate.release.set()
            with self.assertRaisesRegex(ValueError, '任务|变化'):
                await pending
        self.assertEqual(FeedbackStore(self.sessions).get(row['id'])['state'], 'candidate')
        self.assertFalse(FeedbackStore(self.sessions).memories.document_path(row['document']).exists())

    async def test_feedback_confirm_rejects_changed_candidate_content_or_document(self):
        from nailong.core.feedback import FeedbackStore
        from nailong.core.preferences import atomic_json
        for field, value in [('document', 'project/other-target.md'), ('behavior', 'changed behavior')]:
            with self.subTest(field=field):
                row = await self.controller.command('work_control', self.feedback_payload('mutate-'+field))
                store = FeedbackStore(self.sessions)
                gate = PausedFingerprint()
                with patch('nailong.core.verification.input_fingerprint', gate):
                    pending = await self.start_scan('work_control', {'operation': 'feedback_confirm',
                        'branch_id': self.branch['id'], 'id': row['id'], 'expected_revision': row['revision']}, gate)
                    changed = {**row, field: value}
                    atomic_json(store._path(row['id']), changed, store.boundary)
                    gate.release.set()
                    with self.assertRaisesRegex(ValueError, '反馈|变化'):
                        await pending
                self.assertEqual(store.get(row['id'])['state'], 'candidate')
                self.assertFalse(store.memories.document_path(changed['document']).exists())

    async def test_native_feedback_stage_observation_uses_exact_document_and_lock_exclusions(self):
        request_id = 'owned-document'
        identity = hashlib.sha256((str(self.sessions.root)+':'+request_id).encode()).hexdigest()
        relative = '.nailong/memory/feedback-'+identity[:24]+'.md'
        target = self.project/relative
        target.parent.mkdir(parents=True)
        target.write_text('owned topic from an interrupted publication')
        target.with_suffix('.lock').write_text('owned lock')
        (target.parent/'independent.md').write_text('independent experience must remain an input')
        expected = input_fingerprint(self.project, [relative, str(Path(relative).with_suffix('.lock')),
            '.nailong/memory', '.nailong'])
        gate = PausedFingerprint()
        with patch('nailong.core.verification.input_fingerprint', gate):
            pending = await self.start_scan('work_control', self.feedback_payload(request_id), gate)
            gate.release.set()
            row = await pending
        self.assertEqual(row['input_fingerprint'], expected)
        self.assertEqual(target.read_text(), 'owned topic from an interrupted publication')

    async def test_native_feedback_confirm_recovers_owned_publication_and_detects_other_input_changes(self):
        from nailong.core.feedback import FeedbackStore
        store = FeedbackStore(self.sessions)
        row = await self.controller.command('work_control', self.feedback_payload('recover-publication'))
        store.memories.commit_document(store.memories.stage_document(row['document'], store._content(row)))
        gate = PausedFingerprint()
        with patch('nailong.core.verification.input_fingerprint', gate):
            pending = await self.start_scan('work_control', {'operation': 'feedback_confirm',
                'branch_id': self.branch['id'], 'id': row['id'], 'expected_revision': row['revision']}, gate)
            created = await asyncio.wait_for(self.controller.command('create', {'name': 'during publication'}), .3)
            self.assertEqual(created['name'], 'during publication')
            gate.release.set()
            confirmed = await pending
        self.assertEqual(confirmed['state'], 'confirmed')
        second = await self.controller.command('work_control', self.feedback_payload('changed-input'))
        (self.project/'new-input.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, '输入|版本'):
            await self.controller.command('work_control', {'operation': 'feedback_confirm',
                'branch_id': self.branch['id'], 'id': second['id'], 'expected_revision': second['revision']})
        self.assertEqual(store.get(second['id'])['state'], 'candidate')
