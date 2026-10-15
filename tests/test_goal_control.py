"""Current, owner-bound evidence controls progress; operations alone do not."""
import copy
import importlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from nailong.core.goal import GoalStore


def task():
    return {'schema_version': 1, 'task_id': 'work', 'thread_id': 'thread',
        'project_root': '/project', 'revision': 1, 'objective': 'investigate',
        'scope': ['source.py'], 'changed_paths': [], 'lifecycle': 'active',
        'steps': [], 'acceptance': [{'id': 'feature', 'description': 'feature works',
            'kind': 'test', 'required': True, 'status': 'pending', 'evidence_ids': []}],
        'evidence': [], 'pending_verification': [], 'blockers': [],
        'input_fingerprint': 'input-1', 'current_input_fingerprint': 'input-1'}


def add_result(snapshot, *, identifier='result-1', status='passed', **changes):
    evidence = {'id': identifier, 'kind': 'test', 'source': 'runtime', 'status': status,
        'task_revision': snapshot['revision'], 'paths': ['source.py'],
        'input_fingerprint': 'input-1', 'coverage': 'complete',
        'summary': 'observed configured check result', 'artifact_ref': 'verification:run-1'}
    evidence.update(changes)
    snapshot['evidence'].append(evidence)
    snapshot['acceptance'][0].update(status=status, evidence_ids=[identifier])
    return snapshot


class GoalProgressTests(unittest.TestCase):
    def assess(self, before, after):
        try:
            assess = importlib.import_module('nailong.core.goal_control').assess_goal_progress
        except ModuleNotFoundError:
            self.fail('current goal progress assessment is missing')
        return assess(before, after)

    def test_known_unchanged_task_is_no_change_even_after_operations(self):
        before = task(); after = copy.deepcopy(before)
        after.update(changed_paths=['source.py'], progress='model says finished')
        report = self.assess(before, after)
        self.assertEqual(report['status'], 'no_change')
        self.assertEqual(report['evidence_ids'], [])

    def test_real_bound_check_advances_acceptance_without_file_edit(self):
        before = task(); after = add_result(copy.deepcopy(before))
        report = self.assess(before, after)
        self.assertEqual(report['status'], 'progress')
        self.assertEqual(report['evidence_ids'], ['result-1'])
        self.assertEqual(report['acceptance_ids'], ['feature'])
        self.assertEqual(before['evidence'], [])

    def test_qualified_negative_result_is_progress_once(self):
        before = task(); after = add_result(copy.deepcopy(before), status='failed', coverage='partial')
        report = self.assess(before, after)
        self.assertEqual(report['status'], 'progress')
        self.assertEqual(report['reason'], 'new_negative_evidence')
        repeated = copy.deepcopy(after)
        add_result(repeated, identifier='renamed-result', status='failed', coverage='partial',
            summary='a different prose description', artifact_ref='verification:rerun')
        self.assertEqual(self.assess(after, repeated)['status'], 'no_change')

    def test_new_id_for_identical_success_is_not_progress(self):
        before = add_result(task()); after = copy.deepcopy(before)
        add_result(after, identifier='another-id', artifact_ref='verification:rerun')
        self.assertEqual(self.assess(before, after)['status'], 'no_change')

    def test_model_stale_unknown_and_wrong_identity_never_advance(self):
        for changes in ({'source': 'model'}, {'task_revision': 2},
                {'input_fingerprint': 'old'}, {'coverage': 'unknown'},
                {'task_id': 'another-task'}, {'paths': ['unrelated.py']}):
            with self.subTest(changes=changes):
                before = task(); after = add_result(copy.deepcopy(before), **changes)
                report = self.assess(before, after)
                self.assertEqual(report['status'], 'unknown')
                self.assertEqual(report['evidence_ids'], [])
                self.assertEqual(report['acceptance_ids'], [])

    def test_duplicate_evidence_id_cannot_advance(self):
        before = task(); after = add_result(copy.deepcopy(before))
        after['evidence'].append(copy.deepcopy(after['evidence'][0]))
        self.assertEqual(self.assess(before, after)['status'], 'unknown')

    def test_malformed_evidence_stays_unknown_without_raising(self):
        for row in ({'id': ['invalid']}, {'id': 'bad', 'paths': None},
                {'id': 'bad', 'kind': {}, 'source': [], 'paths': ['source.py']}):
            with self.subTest(row=row):
                before = task(); after = copy.deepcopy(before)
                after['evidence'].append(row)
                self.assertEqual(self.assess(before, after)['status'], 'unknown')

    def test_unknown_current_input_is_distinct_from_no_change(self):
        before = task(); after = add_result(copy.deepcopy(before))
        after['current_input_fingerprint'] = None
        self.assertEqual(self.assess(before, after)['status'], 'unknown')
        self.assertEqual(self.assess(None, after)['status'], 'unknown')

    def test_requirement_changes_break_progress_comparison(self):
        before = task(); after = add_result(copy.deepcopy(before))
        after['revision'] = 2
        report = self.assess(before, after)
        self.assertEqual(report['status'], 'unknown')
        self.assertEqual(report['reason'], 'task_revision_changed')

    def test_input_change_with_new_current_evidence_can_advance(self):
        before = add_result(task()); after = copy.deepcopy(before)
        after['current_input_fingerprint'] = 'input-2'
        add_result(after, identifier='new-input-result', input_fingerprint='input-2')
        self.assertEqual(self.assess(before, after)['status'], 'progress')

    def test_new_versioned_read_result_can_advance_research(self):
        before = task(); after = copy.deepcopy(before)
        after['evidence'].append({'id': 'read-1', 'kind': 'read', 'source': 'runtime',
            'status': 'passed', 'task_revision': 1, 'paths': ['source.py'],
            'input_fingerprint': 'input-1', 'coverage': 'partial', 'summary': 'read first page',
            'result_digest': 'page-one'})
        report = self.assess(before, after)
        self.assertEqual(report['status'], 'progress')
        self.assertEqual(report['evidence_ids'], ['read-1'])
        self.assertEqual(report['acceptance_ids'], [])
        paged = copy.deepcopy(after)
        paged['evidence'].append({**after['evidence'][0], 'id': 'read-2', 'result_digest': 'page-two'})
        self.assertEqual(self.assess(after, paged)['status'], 'progress')


class GoalDecisionTests(unittest.TestCase):
    def goal(self, **changes):
        value = {'id': 'goal', 'objective': 'investigate', 'thread_id': 'thread', 'state': 'active',
            'round': 1, 'max_rounds': 10, 'spent_usd': 0.1, 'max_cost_usd': 1.0,
            'no_change_rounds': 0, 'last_progress': {}}
        value.update(changes)
        return SimpleNamespace(**value)

    def decide(self, goal, snapshot, **kwargs):
        try:
            decide = importlib.import_module('nailong.core.goal_control').decide_goal_turn
        except ModuleNotFoundError:
            self.fail('goal preflight decision is missing')
        return decide(goal, snapshot, **kwargs)

    def test_explicit_external_wait_and_manual_acceptance_wait(self):
        waiting = task(); waiting['wait_condition'] = {'reason': 'CI unchanged', 'condition': 'new CI result'}
        result = self.decide(self.goal(), waiting)
        self.assertEqual(result['action'], 'wait')
        self.assertEqual(result['resume_condition'], 'new CI result')
        manual = task(); manual['acceptance'][0]['kind'] = 'manual'
        self.assertEqual(self.decide(self.goal(), manual)['action'], 'wait')

    def test_pause_blocked_and_limits_precede_wait(self):
        waiting = task(); waiting['wait_condition'] = 'new CI result'
        for changes in ({'state': 'paused'}, {'state': 'blocked'}, {'state': 'complete'},
                {'spent_usd': 1.0}, {'round': 10}):
            with self.subTest(changes=changes):
                self.assertEqual(self.decide(self.goal(**changes), waiting)['action'], 'stop')

    def test_no_change_replan_is_bound_to_current_task_revision_and_input(self):
        progress = {'status': 'no_change', 'task_id': 'work', 'task_revision': 1,
            'project_root': '/project', 'current_input_fingerprint': 'input-1',
            'evidence_ids': [], 'acceptance_ids': []}
        goal = self.goal(no_change_rounds=3, last_progress=progress)
        self.assertEqual(self.decide(goal, task())['action'], 'replan')
        changed = task(); changed['revision'] = 2
        self.assertEqual(self.decide(goal, changed)['action'], 'execute')
        changed = task(); changed['current_input_fingerprint'] = 'input-2'
        self.assertEqual(self.decide(goal, changed)['action'], 'execute')
        self.assertEqual(self.decide(goal, task(), progress={'status': 'unknown'})['action'], 'execute')

    def test_unknown_research_can_execute_and_valid_manual_confirmation_resumes(self):
        self.assertEqual(self.decide(self.goal(no_change_rounds=12), None)['action'], 'execute')
        confirmed = task(); confirmed['acceptance'][0]['kind'] = 'manual'
        add_result(confirmed, kind='manual', source='user')
        self.assertEqual(self.decide(self.goal(), confirmed)['action'], 'execute')
        confirmed['current_input_fingerprint'] = 'changed'
        self.assertEqual(self.decide(self.goal(), confirmed)['action'], 'wait')


class GoalProgressPersistenceTests(unittest.TestCase):
    def test_invalid_progress_cannot_break_cost_settlement_or_increase_streak(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory) / 'goals.json')
            goal = store.create('research')
            for index, progress in enumerate(([], {'status': 'no_change'},
                    {'status': 'progress', 'task_id': 'work', 'task_revision': True})):
                with self.subTest(progress=progress):
                    store.record_round(goal.id, cost_usd=.01, files_changed=True,
                        tool_calls=1, round_id=str(index), progress=progress)
                    saved = store.get(goal.id)
                    self.assertEqual(saved.round, index + 1)
                    self.assertAlmostEqual(saved.spent_usd, (index + 1) * .01)
                    self.assertEqual(saved.last_progress['status'], 'unknown')
                    self.assertEqual(saved.no_change_rounds, 0)

    def test_progress_is_bounded_revision_bound_and_duplicate_round_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory) / 'goals.json')
            goal = store.create('research', max_rounds=100)
            record = {'status': 'no_change', 'reason': 'no_current_evidence_delta',
                'task_id': 'work', 'task_revision': 1, 'project_root': '/project',
                'current_input_fingerprint': 'input-1', 'evidence_ids': [], 'acceptance_ids': []}
            try:
                for index in range(15):
                    store.record_round(goal.id, cost_usd=.01, files_changed=False, tool_calls=1,
                        round_id=str(index), progress=record)
            except TypeError:
                self.fail('goal rounds do not persist assessed progress')
            saved = GoalStore(store.path).get(goal.id)
            self.assertEqual(saved.no_change_rounds, 12)
            self.assertEqual(len(saved.recent_progress), 12)
            self.assertEqual(saved.last_progress['status'], 'no_change')
            store.record_round(goal.id, cost_usd=.2, files_changed=True, tool_calls=2,
                round_id='14', progress={**record, 'status': 'progress'})
            self.assertAlmostEqual(store.get(goal.id).spent_usd, .15)
            store.record_round(goal.id, cost_usd=0, files_changed=False, tool_calls=1,
                progress={**record, 'task_revision': 2})
            self.assertEqual(store.get(goal.id).no_change_rounds, 1)
            store.record_round(goal.id, cost_usd=0, files_changed=False, tool_calls=1,
                progress={**record, 'status': 'unknown'})
            self.assertEqual(store.get(goal.id).no_change_rounds, 0)
