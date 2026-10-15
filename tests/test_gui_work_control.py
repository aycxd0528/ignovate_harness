import unittest

import test_gui_controller as fixture
from nailong.core.task_state import TaskStore


class WorkControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.ControllerTests.asyncSetUp(self)
        self.tasks = TaskStore(self.sessions, api_key='sentinel-secret')
        self.controller.service.task_store = self.tasks
        self.task = self.tasks.begin(self.branch['thread_id'], '修复项目入口')

    async def asyncTearDown(self):
        await fixture.ControllerTests.asyncTearDown(self)

    until = fixture.ControllerTests.until

    async def test_snapshot_projects_task_and_attention_without_model(self):
        self.tasks.add_acceptance(self.branch['thread_id'], 'desktop', '实际检查窗口', kind='manual')
        value = self.controller.snapshot()
        self.assertEqual(value['control']['items'][0]['task_id'], self.task['task_id'])
        self.assertTrue(any(row['kind'] == 'manual' for row in value['control']['attention']))
        self.assertEqual(self.controller.runs, {})

    async def test_explicit_link_and_wait_are_revision_checked(self):
        target = await self.controller.command('create', {'name': '接续会话'})
        await self.controller.command('work_control', {'operation': 'link_task',
            'branch_id': target['id'], 'task_id': self.task['task_id'], 'expected_revision': self.task['revision']})
        linked = self.tasks.snapshot(target['thread_id'])
        self.assertEqual(linked['task_id'], self.task['task_id'])
        with self.assertRaises(ValueError):
            await self.controller.command('work_control', {'operation': 'set_wait',
                'branch_id': target['id'], 'reason': '等待 CI', 'condition': 'CI 结果更新', 'expected_revision': -1})
        wait = await self.controller.command('work_control', {'operation': 'set_wait',
            'branch_id': target['id'], 'reason': '等待 CI', 'condition': 'CI 结果更新', 'expected_revision': linked['revision']})
        self.assertEqual(wait['wait_condition']['condition'], 'CI 结果更新')
        self.assertEqual(len(self.controller.runs), 0)

    async def test_completed_run_has_recovery_receipt_and_no_fake_cost(self):
        run = await self.controller.command('send', {'branch_id': self.branch['id'],
            'message': 'hello', 'request_id': 'receipt-test'})
        await self.until(lambda: self.controller.runs[run['run_id']]['status'] == 'completed')
        receipts = self.controller.snapshot()['control']['receipts']
        row = next(row for row in receipts if row['run_id'] == run['run_id'])
        self.assertEqual(row['branch_id'], self.branch['id'])
        self.assertEqual(row['node_id'], self.controller.runs[run['run_id']]['node_id'])
        self.assertIn('closed', [s['stage'] for s in row['stages']])
        self.assertNotIn('cost_settled', [s['stage'] for s in row['stages']])

    async def test_busy_control_mutation_cannot_change_in_flight_task(self):
        run = await self.controller.command('send', {'branch_id': self.branch['id'], 'message': 'wait', 'request_id': 'busy'})
        await self.until(lambda: self.controller.runs[run['run_id']]['status'] == 'running')
        with self.assertRaisesRegex(ValueError, '运行|队列'):
            await self.controller.command('work_control', {'operation': 'set_wait', 'branch_id': self.branch['id'],
                'reason': '等待', 'condition': '新结果', 'expected_revision': self.task['revision']})

    async def test_stale_manual_acceptance_is_visible_before_next_run(self):
        from nailong.core.verification import input_fingerprint
        source = self.project / 'source.py'
        source.write_text('value = 1')
        self.tasks.add_acceptance(self.branch['thread_id'], 'manual', 'inspect output', kind='manual')
        self.tasks.user_decision(self.branch['thread_id'], 'manual', 'reviewed',
            current_input_fingerprint=input_fingerprint(self.project))
        source.write_text('value = 2')
        attention = self.controller.snapshot()['control']['attention']
        self.assertTrue(any(row.get('acceptance_id') == 'manual' for row in attention))

    async def test_current_user_waiver_does_not_request_duplicate_manual_decision(self):
        from nailong.core.verification import input_fingerprint
        self.tasks.add_acceptance(self.branch['thread_id'], 'manual', 'inspect output', kind='manual')
        self.tasks.user_decision(self.branch['thread_id'], 'manual', 'user waived', waive=True,
            current_input_fingerprint=input_fingerprint(self.project))
        self.assertFalse(any(row.get('acceptance_id') == 'manual' for row in self.controller.snapshot()['control']['attention']))

    async def test_feedback_cannot_attribute_old_node_to_new_task(self):
        thread = self.branch['thread_id']
        self.sessions.append_event(thread, 'user', {'text': 'old task'})
        node = self.controller.branches.commit_turn(self.branch['id'], 0, None, task_snapshot=self.task)
        replacement = self.tasks.begin(thread, 'unrelated task', new=True)
        with self.assertRaisesRegex(ValueError, '来源|版本|任务'):
            await self.controller.command('work_control', {'operation': 'feedback_stage',
                'branch_id': self.branch['id'], 'node_id': node['id'],
                'expected_revision': replacement['revision'], 'context': 'context', 'outcome': 'outcome',
                'behavior': 'behavior', 'boundary': 'boundary', 'request_id': 'wrong-source'})

    async def test_native_node_feedback_can_be_confirmed_and_retains_source_input(self):
        from nailong.core.verification import input_fingerprint
        thread = self.branch['thread_id']
        self.tasks.add_acceptance(thread, 'human', 'inspect', kind='manual')
        fingerprint = input_fingerprint(self.project)
        self.tasks.user_decision(thread, 'human', 'reviewed', current_input_fingerprint=fingerprint)
        task = self.tasks.snapshot(thread)
        self.sessions.append_event(thread, 'user', {'text': 'source'})
        node = self.controller.branches.commit_turn(self.branch['id'], 0, None, task_snapshot=task)
        row = await self.controller.command('work_control', {'operation':'feedback_stage',
            'branch_id':self.branch['id'], 'node_id':node['id'], 'expected_revision':task['revision'],
            'context':'context', 'outcome':'outcome', 'behavior':'behavior', 'boundary':'boundary', 'request_id':'node-feedback'})
        self.assertEqual(row['source_input_fingerprint'], fingerprint)
        confirmed = await self.controller.command('work_control', {'operation':'feedback_confirm',
            'branch_id':self.branch['id'], 'id':row['id'], 'expected_revision':row['revision']})
        self.assertEqual(confirmed['state'], 'confirmed')

    async def test_feedback_rejects_changed_known_source_input(self):
        from nailong.core.verification import input_fingerprint
        thread = self.branch['thread_id']
        path = self.project / 'source.py'; path.write_text('before')
        self.tasks.add_acceptance(thread, 'human', 'inspect', kind='manual')
        self.tasks.user_decision(thread, 'human', 'reviewed', current_input_fingerprint=input_fingerprint(self.project))
        task = self.tasks.snapshot(thread)
        self.sessions.append_event(thread, 'user', {'text': 'old source'})
        node = self.controller.branches.commit_turn(self.branch['id'], 0, None, task_snapshot=task)
        path.write_text('after')
        with self.assertRaisesRegex(ValueError, '输入|来源'):
            await self.controller.command('work_control', {'operation':'feedback_stage',
                'branch_id':self.branch['id'], 'node_id':node['id'], 'expected_revision':task['revision'],
                'context':'context', 'outcome':'outcome', 'behavior':'behavior', 'boundary':'boundary', 'request_id':'stale-input'})

    async def test_recovery_excludes_later_cli_turn_from_original_gui_receipt(self):
        from nailong.core.control_ledger import ControlLedger
        self.controller.paused = True
        queued = await self.controller.command('send', {'branch_id':self.branch['id'], 'message':'original', 'request_id':'original'})
        run = self.controller.runs[queued['run_id']]; run.update(status='interrupted', start_index=0)
        thread = self.branch['thread_id']; ledger = ControlLedger(self.sessions)
        ledger.begin(run['id'], thread_id=thread, request_id='original')
        with self.sessions.run_scope(run['id'], thread):
            self.sessions.append_event(thread, 'turn_start', {})
            self.sessions.append_event(thread, 'user', {'text':'original'})
            self.sessions.append_event(thread, 'usage', {'input_tokens':9, 'output_tokens':1,
                'price_snapshot':{'input_per_million':1,'cache_hit_per_million':1,'output_per_million':1}})
            self.sessions.append_event(thread, 'final', {'text':'original result'})
        self.sessions.append_event(thread, 'turn_start', {})
        self.sessions.append_event(thread, 'user', {'text':'later CLI'})
        self.sessions.append_event(thread, 'usage', {'input_tokens':90,'output_tokens':10})
        self.controller._recover_runs()
        node = self.controller.branches.node(run['node_id'])
        self.assertEqual(node['end_index'], 4)
        self.assertEqual(len(self.controller.branches.node_events(node)), 4)
        stages = {s['stage']:s['details'] for s in ledger.get(run['id'])['stages']}
        self.assertEqual(stages['cost_settled']['calls'], 1)

    async def test_receipt_uses_exact_node_and_rejects_estimated_child_usage(self):
        from nailong.core.control_ledger import ControlLedger
        from nailong.gui.work_control import closeout
        thread = self.branch['thread_id']
        self.sessions.append_event(thread, 'user', {'text': 'first'})
        self.sessions.append_event(thread, 'usage', {'scope': 'child', 'estimated': True,
            'calls': [{'input_tokens': 1, 'output_tokens': 1}, {'input_tokens': 2, 'output_tokens': 1}]})
        node = self.controller.branches.commit_turn(self.branch['id'], 0, None, task_snapshot=self.task)
        run = {'id': 'estimated', 'status': 'completed', 'node_id': node['id'], 'tools': [], 'start_index': 0}
        ledger = ControlLedger(self.sessions)
        ledger.begin(run['id'], thread_id=thread, request_id='estimated')
        # Later task and usage must never be attributed to the first run.
        later = self.tasks.begin(thread, 'new task', new=True)
        self.sessions.append_event(thread, 'usage', {'input_tokens': 9, 'output_tokens': 1})
        self.controller.branches.commit_turn(self.branch['id'], node['end_index'], None, task_snapshot=later)
        closeout(self.controller, run, self.branch)
        stages = {s['stage']: s['details'] for s in ledger.get(run['id'])['stages']}
        self.assertNotIn('cost_settled', stages)
        self.assertEqual(stages['task_committed']['task_id'], self.task['task_id'])

    async def test_restart_fills_only_missing_receipt_stages_without_replay(self):
        from nailong.core.control_ledger import ControlLedger
        thread = self.branch['thread_id']
        # A queued record provides the persisted GUI schema, without starting a model.
        self.controller.paused = True
        queued = await self.controller.command('send', {'branch_id': self.branch['id'],
            'message': 'lost-process', 'request_id': 'lost-process'})
        run = self.controller.runs[queued['run_id']]
        run.update(status='interrupted', start_index=len(self.sessions.read_events(thread)))
        ledger = ControlLedger(self.sessions)
        ledger.begin(run['id'], thread_id=thread, request_id='lost-process')
        ledger.record(run['id'], 'effects_observed', details={'tool_calls': 0, 'effect_state': 'observed'})
        with self.sessions.run_scope(run['id'], thread):
            self.sessions.append_event(thread, 'user', {'text': 'lost-process'})
            self.sessions.append_event(thread, 'usage', {'input_tokens': 9, 'output_tokens': 1,
                'price_snapshot': {'input_per_million': 1, 'cache_hit_per_million': 1, 'output_per_million': 1}})
            self.sessions.append_event(thread, 'final', {'text': 'saved'})
        self.controller._save_run(run)
        self.controller._recover_runs()
        recovered = ledger.get(run['id'])
        stages = {s['stage']: s['details'] for s in recovered['stages']}
        self.assertIn('closed', stages)
        self.assertIn('cost_settled', stages)
        self.assertNotIn('task_committed', stages, 'unknown historical task must stay unknown')
        self.assertEqual(stages['effects_observed']['effect_state'], 'observed')
        self.assertIn(run['node_id'], stages['cost_settled']['usage_ref'])
        self.controller._recover_runs()
        self.assertEqual(ledger.get(run['id']), recovered)
