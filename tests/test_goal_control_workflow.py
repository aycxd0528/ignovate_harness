"""Goal control uses native task evidence before calls and after verification."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent import AgentRuntimeFactory
from agent_service import AgentService, TurnEvent
from config import Settings
from nailong.core.costs import CostEstimator
from nailong.core.sessions import ProjectSessionStore
from nailong.core.verification import VerificationService, input_fingerprint
from platform_fixtures import python_command
from test_safety_regressions import RecordingModel, paid_response
from ui.flows import drive_goal


class GoalControlWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.project = tempfile.TemporaryDirectory(); self.data = tempfile.TemporaryDirectory()
        self.root = Path(self.project.name).resolve()
        (self.root / 'source.py').write_text('value = 1\n', newline='\n')
        (self.root / '.nailong').mkdir()
        self.steps = [{'name': 'test', 'kind': 'test', 'command': python_command('pass')}]
        (self.root / '.nailong/settings.json').write_text(json.dumps({'verification': {'steps': self.steps}}), newline='\n')
        self.settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-flash', self.root)
        self.factory = AgentRuntimeFactory(self.settings,
            session_store=ProjectSessionStore(self.root, base_dir=self.data.name))
        self.service = AgentService(self.factory, api_key='fixture-key')
        self.tasks = self.factory.task_store; self.goals = self.factory.goal_store
        self.estimator = CostEstimator('deepseek-flash', self.root)
        self.model = RecordingModel(responses=[paid_response('Observed work remains subject to acceptance.')])
        self.factory.model = self.model
        self.tasks.begin('thread', 'research', scope=['source.py'])
        self.tasks.configure_verification('thread', self.steps)
        self.events = []

    async def asyncTearDown(self):
        await self.factory.aclose(); self.factory.close()
        self.project.cleanup(); self.data.cleanup()

    async def run_goal(self, *, emit=None):
        goal = self.goals.create('research', max_rounds=1, thread_id='thread')
        return await drive_goal(self.service, self.factory, self.goals, self.estimator,
            goal, thread_id='thread', emit=emit or self.events.append,
            approval=lambda *args: 'approve_once')

    async def test_explicit_wait_pauses_before_any_model_call_or_round(self):
        # The TaskStore wait API is implemented by the work-item task. Existing
        # v1 snapshots permit this additional field during parallel integration.
        if callable(getattr(self.tasks, 'set_wait', None)):
            self.tasks.set_wait('thread', reason='CI unchanged', condition='new CI result',
                expected_revision=self.tasks.snapshot('thread')['revision'])
        else:
            snapshot = self.tasks.snapshot('thread')
            snapshot['wait_condition'] = {'reason': 'CI unchanged', 'condition': 'new CI result'}
            (self.tasks.directory / 'thread.json').write_text(json.dumps(snapshot), newline='\n')
        result = await self.run_goal()
        self.assertEqual(len(self.model._calls), 0)
        self.assertEqual(result.round, 0)
        self.assertEqual(result.spent_usd, 0)
        self.assertEqual(result.state, 'paused')
        control = next(event.data for event in self.events if event.kind == 'goal_control')
        self.assertEqual(control['action'], 'wait')
        self.assertEqual(control['resume_condition'], 'new CI result')

    async def test_pending_manual_acceptance_waits_before_stream(self):
        self.tasks.add_acceptance('thread', 'human', 'human review', kind='manual')
        result = await self.run_goal()
        self.assertEqual(len(self.model._calls), 0)
        self.assertEqual(result.round, 0)
        self.assertIn('人工验收', result.pause_reason)

    async def test_user_pause_while_preflight_is_published_prevents_model_call(self):
        def emit(event):
            self.events.append(event)
            if event.kind == 'goal_control' and event.data['action'] == 'execute':
                self.goals.update(event.data['goal_id'], state='paused',
                    summary='user paused', thread_id='thread')
        result = await self.run_goal(emit=emit)
        self.assertEqual(len(self.model._calls), 0)
        self.assertEqual(result.round, 0)
        self.assertEqual(result.state, 'paused')

    async def test_wait_added_while_preflight_is_published_prevents_model_call(self):
        def emit(event):
            self.events.append(event)
            if event.kind == 'goal_control' and event.data['action'] == 'execute':
                self.tasks.set_wait('thread', reason='new external wait', condition='new result',
                    expected_revision=self.tasks.snapshot('thread')['revision'])
        result = await self.run_goal(emit=emit)
        self.assertEqual(len(self.model._calls), 0)
        self.assertEqual(result.round, 0)
        self.assertEqual(result.state, 'paused')

    async def test_pause_during_changed_preflight_publication_prevents_call(self):
        publications = 0
        def emit(event):
            nonlocal publications
            self.events.append(event)
            if event.kind == 'goal_control':
                publications += 1
                if publications == 1:
                    self.tasks.amend('thread', constraints=['updated requirement'])
                elif publications == 2:
                    self.goals.update(event.data['goal_id'], state='paused',
                        summary='user paused', thread_id='thread')
        result = await self.run_goal(emit=emit)
        self.assertEqual(len(self.model._calls), 0)
        self.assertEqual(result.round, 0)

    async def test_native_verification_progress_is_recorded_after_real_check(self):
        self.tasks.add_acceptance('thread', 'feature', 'feature works', kind='test')
        self.tasks.bind_verification('thread', 'feature', 'test', ['source.py'])
        result = await self.run_goal()
        self.assertEqual(result.round, 1)
        self.assertGreater(result.spent_usd, 0)
        self.assertEqual(result.last_progress.get('status'), 'progress')
        self.assertIn('feature', result.last_progress['acceptance_ids'])
        snapshot = self.tasks.snapshot('thread')
        referenced = set(result.last_progress['evidence_ids'])
        self.assertTrue(referenced)
        self.assertTrue(referenced.issubset({row['id'] for row in snapshot['evidence']}))
        self.assertEqual(result.last_progress['current_input_fingerprint'], input_fingerprint(self.root))
        kinds = [event.kind for event in self.events]
        self.assertLess(kinds.index('goal_control'), kinds.index('usage'))
        self.assertLess(kinds.index('verification_step'), kinds.index('goal_progress'))

    async def test_native_file_read_records_versioned_research_progress(self):
        from nailong.core.goal_control import assess_goal_progress
        self.goals.create('research', max_rounds=1, thread_id='thread')
        before = {**self.tasks.snapshot('thread'), 'current_input_fingerprint': input_fingerprint(self.root)}
        self.factory.model = RecordingModel(responses=[paid_response('', tool_calls=[
            {'name': 'read_file', 'args': {'path': 'source.py'}, 'id': 'read-source'}]),
            paid_response('Read the current file.')])
        async for _ in self.service.stream_turn('research', {'configurable': {'thread_id': 'thread'}},
                pin_message='goal', history_display='/goal research', approval_handler=lambda *a: 'approve_once'):
            pass
        after = {**self.tasks.snapshot('thread'), 'current_input_fingerprint': input_fingerprint(self.root)}
        result = assess_goal_progress(before, after)
        self.assertEqual(result['status'], 'progress', (result, after['evidence']))
        reads = [e for e in after['evidence'] if e['kind'] == 'read']
        self.assertTrue(reads)
        self.assertTrue(reads[0].get('result_digest'))
        self.assertEqual(reads[0]['input_fingerprint'], after['current_input_fingerprint'])


    async def test_external_edit_invalidates_manual_confirmation_before_call(self):
        self.tasks.add_acceptance('thread', 'human', 'human review', kind='manual')
        self.tasks.user_decision('thread', 'human', 'reviewed current input',
            current_input_fingerprint=input_fingerprint(self.root))
        (self.root / 'source.py').write_text('value = 2\n', newline='\n')
        result = await self.run_goal()
        self.assertEqual(len(self.model._calls), 0)
        self.assertEqual(result.round, 0)
        self.assertEqual(result.state, 'paused')

    async def test_model_claim_of_new_evidence_is_not_effective_progress(self):
        self.tasks.add_acceptance('thread', 'feature', 'feature works', kind='test')
        self.tasks.bind_verification('thread', 'feature', 'test', ['source.py'])
        # Give the goal a real already-current receipt so verification is cached.
        goal = self.goals.create('research', max_rounds=1, thread_id='thread')
        await VerificationService(self.root, self.factory.session_store, self.goals,
            task_store=self.tasks).run('thread', approval=lambda *args: 'approve_once',
            permission_engine=self.service.permission_engine)
        self.model = RecordingModel(responses=[paid_response('Added evidence ID invented-id; all passed.')])
        self.factory.model = self.model
        result = await drive_goal(self.service, self.factory, self.goals, self.estimator,
            goal, thread_id='thread', emit=self.events.append, approval=lambda *args: 'approve_once')
        self.assertEqual(result.last_progress.get('status'), 'no_change')
        self.assertEqual(result.last_progress['evidence_ids'], [])
        self.assertEqual(result.no_change_rounds, 1)
        self.assertFalse(any(row['id'] == 'invented-id' for row in self.tasks.snapshot('thread')['evidence']))

    async def test_progress_event_failure_still_settles_paid_cost_once(self):
        self.tasks.add_acceptance('thread', 'feature', 'feature works', kind='test')
        goal = self.goals.create('research', max_rounds=1, thread_id='thread')
        def emit(event):
            if event.kind == 'goal_progress':
                raise asyncio.CancelledError()
            self.events.append(event)
        with self.assertRaises(asyncio.CancelledError):
            await drive_goal(self.service, self.factory, self.goals, self.estimator,
                goal, thread_id='thread', emit=emit, approval=lambda *args: 'approve_once')
        saved = self.goals.get(goal.id)
        self.assertEqual(saved.round, 1)
        self.assertEqual(len(saved.settled_round_ids), 1)
        self.assertGreater(saved.spent_usd, 0)
        self.assertEqual(saved.state, 'paused')

    async def test_repeated_real_failure_replans_without_counting_reruns_as_progress(self):
        self.steps[0]['command'] = python_command('raise SystemExit(3)')
        (self.root / '.nailong/settings.json').write_text(json.dumps({'verification': {'steps': self.steps}}), newline='\n')
        self.tasks.configure_verification('thread', self.steps)
        self.tasks.add_acceptance('thread', 'feature', 'feature works', kind='test')
        self.tasks.bind_verification('thread', 'feature', 'test', ['source.py'])
        self.model = RecordingModel(responses=[paid_response('Still researching.') for _ in range(5)])
        self.factory.model = self.model
        goal = self.goals.create('research', max_rounds=5, thread_id='thread')
        result = await drive_goal(self.service, self.factory, self.goals, self.estimator,
            goal, thread_id='thread', emit=self.events.append, approval=lambda *args: 'approve_once')
        self.assertEqual(len(self.model._calls), 5)
        self.assertEqual([row['status'] for row in result.recent_progress],
            ['progress', 'no_change', 'no_change', 'no_change', 'no_change'])
        progress = next(event.data for event in self.events if event.kind == 'goal_progress')
        self.assertEqual(progress['reason'], 'new_negative_evidence')
        self.assertIn('feature', progress['acceptance_ids'])
        decisions = [event.data['action'] for event in self.events if event.kind == 'goal_control']
        self.assertEqual(decisions, ['execute', 'execute', 'execute', 'execute', 'replan'])
        self.assertEqual(result.state, 'paused')

    async def test_legacy_service_without_task_store_keeps_unknown_progress(self):
        class LegacyService:
            async def stream_turn(self, message, config, **kwargs):
                yield TurnEvent('usage', {'input_tokens': 100, 'output_tokens': 10})
                yield TurnEvent('final', {'text': 'legacy'})
        goal = self.goals.create('legacy', max_rounds=1, thread_id='legacy')
        result = await drive_goal(LegacyService(), SimpleNamespace(), self.goals,
            self.estimator, goal, thread_id='legacy', emit=self.events.append)
        self.assertEqual(result.round, 1)
        self.assertEqual(result.last_progress.get('status'), 'unknown')
        self.assertEqual(result.no_change_rounds, 0)
