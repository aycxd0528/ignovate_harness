"""Real local workflow boundaries: durable identity, approval and cancellation."""
import asyncio
import copy
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nailong.core.delivery import build_delivery_report
from nailong.core.goal import GoalStore
from nailong.core.permissions import ApprovalDecision, PermissionEngine
from nailong.core.progress import assess_progress
from nailong.core.runner import SessionRunner
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
from nailong.core.verification import VerificationService, input_fingerprint, verify_goal_if_configured


class WorkflowFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.project = tempfile.TemporaryDirectory()
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.project.cleanup)
        self.addCleanup(self.data.cleanup)
        self.root = Path(self.project.name).resolve()
        self.store = ProjectSessionStore(self.root, base_dir=self.data.name)
        self.tasks = TaskStore(self.store)
        self.goals = GoalStore(Path(self.data.name) / 'goals.json', project_root=self.root)
        self.goals.task_store = self.tasks
        self.engine = PermissionEngine(self.root, rules={})
        (self.root / '.nailong').mkdir()
        (self.root / 'source.py').write_text('value = 1\n')
        self.steps = [{'name': 'test', 'kind': 'test', 'command': self.command('pass')}]
        self.configure(self.steps)

    def command(self, body):
        return shlex.join([sys.executable, '-B', '-c', body])

    def configure(self, steps):
        (self.root / '.nailong/settings.json').write_text(json.dumps({'verification': {'steps': steps}}))

    def begin(self, *, scope=None, objective='修复功能'):
        self.tasks.begin('thread', objective, scope=scope)
        self.tasks.configure_verification('thread', self.steps)
        return self.tasks.snapshot('thread')

    def verification(self, *, goals=True):
        return VerificationService(self.root, self.store, self.goals if goals else None, task_store=self.tasks)

    async def verify(self, *, approval=None, goals=True):
        return await self.verification(goals=goals).run('thread', approval=approval or (lambda *args: 'approve_once'),
            permission_engine=self.engine)

    async def wait_file(self, name, operation):
        async def wait():
            while not (self.root / name).exists():
                if operation.done():
                    await operation
                    self.fail('操作在创建标记前已结束。')
                await asyncio.sleep(.005)
        await asyncio.wait_for(wait(), 3)


class DurableTaskTests(WorkflowFixture):
    async def test_bound_task_serializes_other_process_requirement_updates(self):
        original = self.begin()
        ready, done = Path(self.data.name) / 'ready', Path(self.data.name) / 'done'
        code = """import sys
from pathlib import Path
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
tasks=TaskStore(ProjectSessionStore(sys.argv[1],base_dir=sys.argv[2]))
Path(sys.argv[3]).write_text('ready')
tasks.amend('thread',constraints=['来自第二进程的要求'])
Path(sys.argv[4]).write_text('done')
"""
        process = None
        try:
            with self.tasks.bound_task('thread', original['task_id'], original['revision']):
                process = subprocess.Popen([sys.executable, '-B', '-c', code, str(self.root), self.data.name,
                    str(ready), str(done)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                deadline = time.monotonic() + 3
                while not ready.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail(process.communicate()[1])
                    time.sleep(.005)
                self.assertTrue(ready.exists())
                self.assertFalse(done.exists())
                self.tasks.set_state('thread', progress='本进程原子写入')
            _, error = process.communicate(timeout=4)
            self.assertEqual(process.returncode, 0, error)
            restored = self.tasks.snapshot('thread')
            self.assertEqual(restored['constraints'], ['来自第二进程的要求'])
            self.assertEqual(restored['progress'], '本进程原子写入')
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=4)

    async def test_bound_task_checks_identity_and_revision_inside_reentrant_lock(self):
        original = self.begin()
        with self.tasks.bound_task('thread', original['task_id'], original['revision']) as matched:
            self.assertEqual(matched['objective'], '修复功能')
            self.tasks.set_state('thread', progress='在同锁内更新')
        self.tasks.amend('thread', constraints=['新版本'])
        with self.tasks.bound_task('thread', original['task_id'], original['revision']) as stale:
            self.assertIsNone(stale)
        current = self.tasks.begin('thread', '新的任务', new=True)
        with self.tasks.bound_task('thread', original['task_id']) as replaced:
            self.assertIsNone(replaced)
        self.assertEqual(self.tasks.snapshot('thread')['task_id'], current['task_id'])

    async def test_review_record_binds_actual_current_evidence_without_model_confirmation(self):
        self.tasks.begin('thread', '审查源码', profile='review', scope=['source.py'])
        task = self.tasks.snapshot('thread')
        fingerprint = input_fingerprint(self.root)
        evidence = {'id': 'review-run', 'kind': 'review', 'source': 'runtime', 'status': 'passed',
            'coverage': 'complete', 'paths': ['source.py'], 'task_revision': task['revision'],
            'input_fingerprint': fingerprint, 'summary': '完整静态流程覆盖', 'artifact_ref': 'review:run'}
        updated = self.tasks.record_review('thread', evidence, expected_task_id=task['task_id'],
            expected_revision=task['revision'])
        self.assertEqual(updated['input_fingerprint'], fingerprint)
        self.assertEqual(build_delivery_report(updated, current_input_fingerprint=fingerprint)['status'], 'reviewed')
        original = copy.deepcopy(updated)
        for changes in ({'source': 'model'}, {'input_fingerprint': 'unknown'}, {'coverage': 'partial'},
                {'paths': ['other.py']}, {'task_revision': task['revision'] + 1}, {'status': 'unknown'}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.tasks.record_review('thread', {**evidence, 'id': 'invalid', **changes},
                        expected_task_id=task['task_id'], expected_revision=task['revision'])
                self.assertEqual(self.tasks.snapshot('thread'), original)

    async def test_stale_review_cannot_mutate_new_requirements_or_new_task(self):
        self.tasks.begin('thread', '审查源码', profile='review', scope=['source.py'])
        task = self.tasks.snapshot('thread')
        evidence = {'kind': 'review', 'source': 'runtime', 'status': 'passed', 'coverage': 'complete',
            'paths': ['source.py'], 'task_revision': task['revision'], 'input_fingerprint': input_fingerprint(self.root),
            'summary': '完整静态流程覆盖'}
        self.tasks.amend('thread', constraints=['新要求'])
        amended = self.tasks.snapshot('thread')
        self.assertIsNone(self.tasks.record_review('thread', evidence, expected_task_id=task['task_id'],
            expected_revision=task['revision']))
        self.assertEqual(self.tasks.snapshot('thread'), amended)
        replacement = self.tasks.begin('thread', '独立任务', new=True)
        self.assertIsNone(self.tasks.record_review('thread', evidence, expected_task_id=task['task_id'],
            expected_revision=task['revision']))
        self.assertEqual(self.tasks.snapshot('thread'), replacement)

    async def test_review_storage_limit_keeps_entire_previous_snapshot(self):
        task = self.tasks.begin('thread', '审查源码', profile='review', scope=['source.py'])
        evidence = {'kind': 'review', 'source': 'runtime', 'status': 'passed', 'coverage': 'complete',
            'paths': ['source.py'], 'task_revision': task['revision'], 'input_fingerprint': input_fingerprint(self.root),
            'summary': '审' * 2000, 'artifact_ref': 'review:large'}
        previous_bytes = len((json.dumps(task, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode())
        with patch('nailong.core.task_state.MAX_TASK_BYTES', previous_bytes + 1500):
            with self.assertRaises(ValueError):
                self.tasks.record_review('thread', evidence, expected_task_id=task['task_id'],
                    expected_revision=task['revision'])
        self.assertEqual(self.tasks.snapshot('thread'), task)

    async def test_fresh_process_restores_requirements_without_replaying_command(self):
        self.begin()
        self.tasks.amend('thread', constraints=['禁止联网', '只修改 source.py'])
        self.tasks.set_step('thread', 'edit', title='修改实现', state='doing')
        self.tasks.record_change('thread', 'source.py')
        self.tasks.reconcile('thread', interrupted=True)
        code = """import json,sys
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
store=ProjectSessionStore(sys.argv[1],base_dir=sys.argv[2])
print(json.dumps(TaskStore(store).snapshot('thread'),ensure_ascii=False))
"""
        restored = json.loads(subprocess.run([sys.executable, '-B', '-c', code, str(self.root), self.data.name],
            capture_output=True, text=True, timeout=4, check=True).stdout)
        self.assertEqual(restored['objective'], '修复功能')
        self.assertEqual(restored['constraints'], ['禁止联网', '只修改 source.py'])
        self.assertEqual(restored['lifecycle'], 'paused')
        self.assertEqual(restored['steps'][0]['state'], 'doing')
        self.assertEqual(restored['changed_paths'], ['source.py'])
        self.assertFalse((self.root / 'ran').exists())

    async def test_new_requirement_preserves_objective_and_invalidates_previous_proof(self):
        self.begin(scope=['source.py'])
        self.tasks.add_acceptance('thread', 'feature', '人工检查行为', kind='manual')
        fingerprint = input_fingerprint(self.root)
        self.tasks.user_decision('thread', 'feature', '实际检查完成', current_input_fingerprint=fingerprint)
        previous = self.tasks.snapshot('thread')
        current = self.tasks.begin('thread', '修复功能', latest_request='后续只读，不要再执行命令。')
        self.assertEqual(current['objective'], '修复功能')
        self.assertEqual(current['latest_request'], '后续只读，不要再执行命令。')
        self.assertIn('后续只读，不要再执行命令。', current['request_history'])
        self.assertEqual(current['revision'], previous['revision'] + 1)
        self.assertEqual(next(row['status'] for row in current['acceptance'] if row['id'] == 'feature'), 'stale')

    async def test_task_cannot_complete_without_current_independent_input_hash(self):
        self.begin(scope=['source.py'])
        self.tasks.add_acceptance('thread', 'feature', '人工检查行为', kind='manual')
        fingerprint = input_fingerprint(self.root)
        self.tasks.user_decision('thread', 'feature', '实际检查完成', current_input_fingerprint=fingerprint)
        with self.assertRaises(ValueError):
            self.tasks.set_state('thread', lifecycle='completed')
        self.assertNotEqual(self.tasks.snapshot('thread')['lifecycle'], 'completed')

    async def test_model_prose_evidence_cannot_promote_acceptance(self):
        self.begin(scope=['source.py'])
        self.tasks.add_acceptance('thread', 'feature', '功能正确', kind='test')
        self.tasks.record_evidence('thread', {'kind': 'test', 'source': 'model', 'status': 'passed',
            'coverage': 'complete', 'paths': ['source.py'], 'input_fingerprint': input_fingerprint(self.root),
            'summary': '模型说全部通过'}, acceptance_ids=['feature'])
        self.assertEqual(next(row['status'] for row in self.tasks.snapshot('thread')['acceptance']
            if row['id'] == 'feature'), 'pending')

    async def test_historical_verification_keeps_current_phase_and_lifecycle(self):
        task = self.begin()
        result = await self.verify()
        self.tasks.amend('thread', constraints=['新的约束'])
        current = self.tasks.snapshot('thread')
        self.tasks.record_verification('thread', {**result, 'run_id': 'late-old-run', 'status': 'cancelled'})
        restored = self.tasks.snapshot('thread')
        self.assertEqual(restored['revision'], current['revision'])
        self.assertEqual(restored['phase'], 'investigate')
        self.assertEqual(restored['lifecycle'], 'active')
        self.assertEqual(restored['acceptance'], current['acceptance'])
        self.assertEqual(result['task_id'], task['task_id'])

    async def test_optional_verification_binding_rejects_corrupt_persisted_shape(self):
        self.begin(scope=['source.py'])
        self.tasks.add_acceptance('thread', 'feature', '功能正确', kind='test')
        self.tasks.bind_verification('thread', 'feature', 'test', ['source.py'])
        original = self.tasks.snapshot('thread')
        bad_values = [None, [], {'step': 'test', 'paths': ['source.py'], 'source': 'model'},
            {'step': 'test', 'paths': 'source.py', 'source': 'user'},
            {'step': 'test', 'paths': [], 'source': 'user'},
            {'step': '', 'paths': ['source.py'], 'source': 'user'},
            {'step': 'test', 'paths': ['../outside'], 'source': 'user'},
            {'step': 'test', 'paths': ['.env'], 'source': 'user'},
            {'step': 'test', 'paths': ['source.py'], 'source': 'user', 'extra': True}]
        path = self.tasks.directory / 'thread.json'
        for bad in bad_values:
            with self.subTest(binding=bad):
                damaged = copy.deepcopy(original)
                next(row for row in damaged['acceptance'] if row['id'] == 'feature')['verification_binding'] = bad
                path.write_text(json.dumps(damaged))
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    TaskStore(self.store).snapshot('thread')
                self.assertEqual(path.read_bytes(), before)
        path.write_text(json.dumps(original))


class ApprovalBoundaryTests(WorkflowFixture):
    async def test_explicit_session_approval_remains_valid_when_it_grants_allow(self):
        self.begin()
        def approve(action, *args):
            return ApprovalDecision('approve_session', self.engine.suggest_rule('run_command', action['args']))
        result = await self.verify(approval=approve)
        self.assertEqual(result['status'], 'passed')
        self.assertTrue(result['steps'][0]['started'])

    async def test_slow_input_hashes_leave_event_loop_responsive(self):
        self.begin()
        ticks, during_scans = 0, []
        async def heartbeat():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(.005)
        def slow_fingerprint(*args):
            before = ticks
            time.sleep(.08)  # Controlled filesystem latency; compute the real hash afterward.
            during_scans.append(ticks > before)
            return input_fingerprint(*args)
        monitor = asyncio.create_task(heartbeat())
        try:
            await asyncio.sleep(.01)
            with patch('nailong.core.verification.input_fingerprint', slow_fingerprint):
                result = await self.verify()
            self.assertEqual(result['status'], 'passed')
            self.assertTrue(during_scans)
            self.assertTrue(all(during_scans), '输入摘要在事件循环线程中阻止了心跳。')
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)

    async def changing_approval(self, mutate):
        self.steps = [{'name': 'test', 'kind': 'test', 'command': self.command("open('ran','w').write('x')"),
            'generated_paths': ['ran']}]
        self.configure(self.steps)
        self.begin()
        arrived, release = asyncio.Event(), asyncio.Event()
        async def approval(*args):
            arrived.set()
            await release.wait()
            return 'approve_once'
        operation = asyncio.create_task(self.verify(approval=approval))
        try:
            await asyncio.wait_for(arrived.wait(), 3)
            mutate()
            release.set()
            result = await asyncio.wait_for(operation, 3)
        finally:
            if not operation.done():
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
        self.assertFalse((self.root / 'ran').exists())
        self.assertFalse(result['steps'][0]['started'])
        self.assertEqual(result['steps'][0]['error_code'], 'approval_invalidated')
        return result

    async def test_input_changed_during_async_approval_never_starts_process(self):
        await self.changing_approval(lambda: (self.root / 'source.py').write_text('value = 2\n'))

    async def test_configuration_changed_during_async_approval_never_starts_process(self):
        await self.changing_approval(lambda: self.configure([{**self.steps[0], 'timeout_seconds': 2}]))

    async def test_task_replaced_during_async_approval_never_starts_process(self):
        await self.changing_approval(lambda: self.tasks.begin('thread', '新任务', new=True))
        self.assertEqual(self.tasks.snapshot('thread')['objective'], '新任务')
        self.assertEqual(self.tasks.snapshot('thread')['lifecycle'], 'active')

    async def test_requirement_changed_during_async_approval_never_starts_process(self):
        await self.changing_approval(lambda: self.tasks.amend('thread', constraints=['新要求']))

    async def test_permission_denied_during_async_approval_never_starts_process(self):
        await self.changing_approval(lambda: self.engine.add_rule('deny', 'Bash(*)'))

    async def test_different_ask_policy_cannot_reuse_previous_approval(self):
        await self.changing_approval(lambda: self.engine.add_rule('ask', 'Bash(*)'))

    async def test_denied_and_unattended_verification_have_zero_side_effects(self):
        self.configure([{'name': 'test', 'kind': 'test', 'command': self.command("open('ran','w').write('x')"),
            'generated_paths': ['ran']}])
        self.begin()
        for permission in ('default', 'plan'):
            result = await self.verification().run('thread', permission_engine=self.engine, permission_mode=permission)
            self.assertFalse(result['steps'][0]['started'])
            self.assertEqual(result['status'], 'unverified')
            self.assertFalse((self.root / 'ran').exists())


class VerificationIdentityTests(WorkflowFixture):
    async def assert_completion_rejects_replacement_during_hash(self, replace_on_call):
        self.begin(scope=['source.py'])
        self.tasks.add_acceptance('thread', 'feature', '功能正确', kind='test')
        self.tasks.bind_verification('thread', 'feature', 'test', ['source.py'])
        goal = self.goals.create('修复功能', thread_id='thread')
        await self.verify()
        other_store = TaskStore(self.store)
        calls = 0
        replacement = None

        def replace_during_hash(*args):
            nonlocal calls, replacement
            fingerprint = input_fingerprint(*args)
            calls += 1
            if calls == replace_on_call:
                other_store.begin('thread', '修复功能', scope=['source.py'], new=True)
                other_store.add_acceptance('thread', 'manual', '用户检查正确', kind='manual')
                replacement = other_store.user_decision('thread', 'manual', '实际检查完成',
                    current_input_fingerprint=fingerprint)
                self.assertEqual(build_delivery_report(replacement,
                    current_input_fingerprint=fingerprint)['status'], 'verified')
            return fingerprint

        with patch('nailong.core.verification.input_fingerprint', replace_during_hash):
            accepted, _, updated = self.goals.update(goal.id, state='complete', thread_id='thread')
        self.assertIsNotNone(replacement)
        self.assertFalse(accepted)
        self.assertEqual(updated.state, 'active')
        self.assertEqual(other_store.snapshot('thread'), replacement)

    async def test_goal_completion_rejects_replacement_during_task_input_hash(self):
        await self.assert_completion_rejects_replacement_during_hash(1)

    async def test_goal_completion_rejects_replacement_during_goal_input_hash(self):
        await self.assert_completion_rejects_replacement_during_hash(2)

    async def test_cancelled_old_process_does_not_pause_replacement_task(self):
        self.steps = [{'name': 'test', 'kind': 'test', 'command': self.command(
            "import os,time;open('pid','w').write(str(os.getpid()));time.sleep(30)"), 'generated_paths': ['pid']}]
        self.configure(self.steps)
        original = self.begin()
        operation = asyncio.create_task(self.verify())
        try:
            await self.wait_file('pid', operation)
            pid = int((self.root / 'pid').read_text())
            replacement = self.tasks.begin('thread', '新的独立任务', new=True)
            operation.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await operation
            current = self.tasks.snapshot('thread')
            self.assertEqual(current['task_id'], replacement['task_id'])
            self.assertEqual(current['lifecycle'], 'active')
            self.assertEqual(current['evidence'], [])
            record = self.store.read_events('thread')[-1]['data']
            self.assertEqual(record['status'], 'cancelled')
            self.assertEqual(record['task_id'], original['task_id'])
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            if not operation.done():
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)

    async def test_successful_user_bound_verification_completes_matching_task_and_goal(self):
        self.begin(scope=['source.py'])
        self.tasks.add_acceptance('thread', 'feature', '功能正确', kind='test')
        self.tasks.bind_verification('thread', 'feature', 'test', ['source.py'])
        goal = self.goals.create('修复功能', thread_id='thread')
        result = await self.verify()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(build_delivery_report(self.tasks.snapshot('thread'),
            current_input_fingerprint=result['input_after'])['status'], 'verified')
        complete, _, current = self.goals.update(goal.id, state='complete', thread_id='thread')
        self.assertTrue(complete)
        self.assertEqual(current.state, 'complete')
        self.assertEqual(self.tasks.snapshot('thread')['lifecycle'], 'completed')

    async def test_narrow_binding_does_not_claim_uncovered_task_scope(self):
        self.begin(scope=['source.py', 'other.py'])
        (self.root / 'other.py').write_text('other = 1\n')
        self.tasks.add_acceptance('thread', 'feature', '功能正确', kind='test')
        self.tasks.bind_verification('thread', 'feature', 'test', ['source.py'])
        result = await self.verify()
        report = build_delivery_report(self.tasks.snapshot('thread'), current_input_fingerprint=result['input_after'])
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(report['status'], 'unverified')
        with self.assertRaises(ValueError):
            self.tasks.set_state('thread', lifecycle='completed', current_input_fingerprint=result['input_after'])

    async def test_goal_cache_revalidates_task_revision_even_when_files_are_unchanged(self):
        self.begin(scope=['source.py'])
        goal = self.goals.create('修复功能', thread_id='thread')
        initial = await self.verify()
        factory = SimpleNamespace(settings=SimpleNamespace(project_root=self.root, api_key=''),
            goal_store=self.goals, task_store=self.tasks)
        service = SimpleNamespace(session_store=self.store, permission_engine=self.engine, permission_mode='default')
        self.assertIsNone(await verify_goal_if_configured(service, factory, goal, 'thread', approval=lambda *args: 'approve_once'))
        self.tasks.amend('thread', constraints=['新增要求'])
        rerun = await verify_goal_if_configured(service, factory, goal, 'thread', approval=lambda *args: 'approve_once')
        self.assertEqual(rerun['status'], 'passed')
        self.assertNotEqual(rerun['run_id'], initial['run_id'])
        self.assertGreater(rerun['task_revision'], initial['task_revision'])

    async def test_stale_goal_proof_cannot_complete_with_a_different_revision_task_proof(self):
        self.begin(scope=['source.py'])
        self.tasks.add_acceptance('thread', 'feature', '功能正确', kind='test')
        self.tasks.bind_verification('thread', 'feature', 'test', ['source.py'])
        goal = self.goals.create('修复功能', thread_id='thread')
        await self.verify()
        self.tasks.amend('thread', constraints=['新的要求'])
        result = await self.verify(goals=False)
        self.assertEqual(build_delivery_report(self.tasks.snapshot('thread'),
            current_input_fingerprint=result['input_after'])['status'], 'verified')
        self.assertFalse(self.goals.update(goal.id, state='complete', thread_id='thread')[0])
        self.assertNotEqual(self.tasks.snapshot('thread')['lifecycle'], 'completed')


class ProgressAndRunnerTests(unittest.IsolatedAsyncioTestCase):
    def read(self, cursor='0'):
        return {'tool': 'read_file', 'arguments_digest': cursor, 'result_digest': 'same',
            'input_version': 'v1', 'ok': True, 'changed': False}

    async def test_identical_successful_reads_hint_then_pause(self):
        rows = []
        actions = []
        for _ in range(6):
            result = assess_progress(rows, self.read())
            actions.append(result['action'])
            rows = result['observations']
        self.assertEqual(actions, ['none', 'none', 'hint', 'none', 'none', 'pause'])

    async def test_paging_polling_and_new_versions_are_not_stopped(self):
        rows = []
        observations = [self.read(str(i)) for i in range(20)]
        observations += [{**self.read(), 'tool': 'run_command'}] * 20
        observations += [{**self.read(), 'input_version': str(i)} for i in range(20)]
        for observation in observations:
            result = assess_progress(rows, observation)
            self.assertEqual(result['action'], 'none')
            rows = result['observations']

    async def test_stop_preserves_queue_when_current_coroutine_suppresses_cancellation(self):
        runner = SessionRunner('project', 'thread')
        self.addAsyncCleanup(runner.close)
        started, cleanup = asyncio.Event(), asyncio.Event()
        seen = []
        async def current():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(.01)
                cleanup.set()
                return '已清理'
        async def next_item():
            seen.append('next')
        first = runner.submit('first', current)
        await asyncio.wait_for(started.wait(), 2)
        second = runner.submit('second', next_item)
        await asyncio.wait_for(runner.stop(), 2)
        self.assertTrue(cleanup.is_set())
        self.assertEqual(await first, '已清理')
        self.assertEqual(runner.state, 'paused')
        self.assertEqual(seen, [])
        self.assertFalse(second.done())
        runner.resume()
        await asyncio.wait_for(second, 2)
        self.assertEqual(seen, ['next'])

    async def test_synchronous_callback_failure_settles_future_and_runs_next_item(self):
        runner = SessionRunner('project', 'thread')
        self.addAsyncCleanup(runner.close)
        runner.state = 'paused'
        def failing():
            raise ValueError('回调失败')
        async def following():
            return 'next'
        failed = runner.submit('failed', failing)
        success = runner.submit('next', following)
        runner.resume()
        with self.assertRaisesRegex(ValueError, '回调失败'):
            await asyncio.wait_for(failed, .3)
        self.assertEqual(await asyncio.wait_for(success, .3), 'next')


if __name__ == '__main__':
    unittest.main()
