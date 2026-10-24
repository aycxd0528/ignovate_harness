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

    async def test_linked_empty_conversation_cannot_displace_real_task_source(self):
        owner=self.controller.branches.create('任务来源',identity='z-owner')
        linked=self.controller.branches.create('关联空会话',identity='a-linked')
        task=self.tasks.begin(owner['thread_id'],'保留真实来源和反馈版本')
        self.tasks.add_acceptance(owner['thread_id'],'manual-source','核对来源',kind='manual')
        task=self.tasks.snapshot(owner['thread_id'])
        self.sessions.append_event(owner['thread_id'],'user',{'text':'真实来源要求'})
        self.sessions.append_event(owner['thread_id'],'final',{'text':'真实来源结果'})
        node=self.controller.branches.commit_turn(owner['id'],0,None,task_snapshot=task)
        await self.controller.command('work_control',{'operation':'link_task','branch_id':linked['id'],
            'task_id':task['task_id'],'expected_revision':task['revision']})
        current=self.tasks.snapshot(linked['thread_id'])
        self.assertEqual(current['associated_thread_ids'],['a-linked','z-owner'])
        from nailong.gui.work_control import projection
        for branches,expected in (([linked],linked['id']),([],None)):
            with self.subTest(available=expected):
                limited=projection(self.controller,branches=branches)
                route=next(row for row in limited['items'] if row['task_id']==task['task_id'])
                self.assertEqual(route['branch_id'],expected)
        snapshot=await self.controller.snapshot_async()
        row=next(row for row in snapshot['control']['items'] if row['task_id']==task['task_id'])
        self.assertEqual(row['branch_id'],owner['id'],'the first alphabetic association is not the recorded source')
        attention=next(row for row in snapshot['control']['attention'] if row.get('task_id')==task['task_id'])
        self.assertEqual(attention['branch_id'],owner['id'])
        feedback=await self.controller.command('work_control',{'operation':'feedback_stage',
            'branch_id':row['branch_id'],'node_id':node['id'],'task_id':task['task_id'],
            'expected_revision':task['revision'],'context':'source','outcome':'observed',
            'behavior':'keep source','boundary':'same task','request_id':'linked-source'})
        self.assertEqual(feedback['source_ref'],f"session:{owner['thread_id']}#node:{node['id']}")
        self.assertEqual(feedback['state'],'candidate')
        self.assertEqual(self.controller.runs,{})

    async def test_owner_new_task_keeps_old_control_routed_to_active_association(self):
        owner=self.controller.branches.create('原始会话',identity='a-owner')
        linked=self.controller.branches.create('继续旧任务',identity='z-linked')
        task=self.tasks.begin(owner['thread_id'],'原始任务')
        await self.controller.command('work_control',{'operation':'link_task','branch_id':linked['id'],
            'task_id':task['task_id'],'expected_revision':task['revision']})
        replacement=self.tasks.begin(owner['thread_id'],'独立新任务',new=True)
        snapshot=await self.controller.snapshot_async()
        row=next(row for row in snapshot['control']['items'] if row['task_id']==task['task_id'])
        self.assertEqual(row['branch_id'],linked['id'])
        self.assertEqual(row['associated_thread_ids'],['z-linked'])
        await self.controller.command('work_control',{'operation':'set_wait','branch_id':row['branch_id'],
            'task_id':task['task_id'],'expected_revision':task['revision'],'reason':'旧任务等待','condition':'旧任务输入'})
        self.assertEqual(self.tasks.snapshot(linked['thread_id'])['wait_condition']['reason'],'旧任务等待')
        self.assertEqual(self.tasks.snapshot(owner['thread_id'])['task_id'],replacement['task_id'])
        self.assertIsNone(self.tasks.snapshot(owner['thread_id']).get('wait_condition'))
        self.assertEqual(self.controller.runs,{})

    async def test_old_task_identity_cannot_control_same_revision_replacement(self):
        from nailong.core.feedback import FeedbackStore
        thread=self.branch['thread_id']
        for operation in ('set_wait','clear_wait','confirm_acceptance','feedback_stage','feedback_confirm'):
            with self.subTest(operation=operation):
                self.tasks.begin(thread,'旧控制来源 '+operation,new=True)
                self.tasks.add_acceptance(thread,'same-id','人工检查',kind='manual')
                old=self.tasks.snapshot(thread)
                self.tasks.begin(thread,'独立替代任务 '+operation,new=True)
                self.tasks.add_acceptance(thread,'same-id','人工检查',kind='manual')
                current=self.tasks.snapshot(thread)
                self.assertEqual(current['revision'],old['revision'])
                extra={}
                if operation=='set_wait':extra={'reason':'wait','condition':'ready'}
                elif operation=='clear_wait':
                    current=self.tasks.set_wait(thread,reason='wait',condition='ready',expected_revision=current['revision'])
                    extra={'expected_wait_condition':current['wait_condition']}
                elif operation=='confirm_acceptance':extra={'acceptance_id':'same-id','reason':'reviewed'}
                else:
                    start=len(self.sessions.read_events(thread))
                    self.sessions.append_event(thread,'user',{'text':'替代任务的真实来源'})
                    node=self.controller.branches.commit_turn(self.branch['id'],start,None,task_snapshot=current)
                    extra={'node_id':node['id'],'context':'context','outcome':'outcome',
                        'behavior':'behavior','boundary':'boundary','request_id':'stale-'+operation}
                    if operation=='feedback_confirm':
                        row=await self.controller.command('work_control',{'operation':'feedback_stage',
                            'branch_id':self.branch['id'],'task_id':current['task_id'],
                            'expected_revision':current['revision'],**extra})
                        extra={'id':row['id'],'expected_revision':row['revision']}
                before=self.tasks.snapshot(thread)
                events=self.sessions.read_events(thread)
                feedback=FeedbackStore(self.sessions).list()
                with self.assertRaisesRegex(ValueError,'任务|身份|版本'):
                    await self.controller.command('work_control',{'operation':operation,
                        'branch_id':self.branch['id'],'task_id':old['task_id'],
                        'expected_revision':old['revision'],**extra})
                self.assertEqual(self.tasks.snapshot(thread),before)
                self.assertEqual(self.sessions.read_events(thread),events)
                self.assertEqual(FeedbackStore(self.sessions).list(),feedback)
        self.assertEqual(self.controller.runs,{})

    async def test_historical_feedback_can_still_be_revoked_after_owner_new_task(self):
        thread=self.branch['thread_id']
        self.sessions.append_event(thread,'user',{'text':'真实历史来源'})
        node=self.controller.branches.commit_turn(self.branch['id'],0,None,task_snapshot=self.task)
        row=await self.controller.command('work_control',{'operation':'feedback_stage',
            'branch_id':self.branch['id'],'node_id':node['id'],'task_id':self.task['task_id'],
            'expected_revision':self.task['revision'],'context':'context','outcome':'outcome',
            'behavior':'behavior','boundary':'boundary','request_id':'historical-revoke'})
        confirmed=await self.controller.command('work_control',{'operation':'feedback_confirm',
            'branch_id':self.branch['id'],'task_id':self.task['task_id'],'id':row['id'],
            'expected_revision':row['revision']})
        self.assertEqual(confirmed['state'],'confirmed')
        replacement=self.tasks.begin(thread,'独立新任务',new=True)
        revoked=await self.controller.command('work_control',{'operation':'feedback_revoke',
            'branch_id':self.branch['id'],'task_id':self.task['task_id'],'id':row['id'],
            'expected_revision':confirmed['revision']})
        self.assertEqual(revoked['state'],'revoked')
        self.assertEqual(self.tasks.snapshot(thread),replacement)

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


class LazyWorkControlTests(unittest.TestCase):
    def test_lazy_manual_and_feedback_controls_reuse_bound_task_store(self):
        import subprocess
        import sys
        import tempfile
        import textwrap
        from pathlib import Path
        import nailong.gui.work_control as work_control

        code = textwrap.dedent("""
            import asyncio
            import sys
            from pathlib import Path
            sys.path.insert(0, sys.argv[1])
            from nailong.core.sessions import ProjectSessionStore
            from nailong.core.task_state import TaskStore
            from nailong.gui.controller import GUIController

            async def run():
                root = Path(sys.argv[2])
                project = root / 'project'
                project.mkdir()
                sessions = ProjectSessionStore(project, base_dir=root / 'data')
                controller = GUIController(project, sessions=sessions, service=None)
                try:
                    branch = controller.branches.create('lazy controls')
                    thread = branch['thread_id']
                    tasks = TaskStore(sessions)
                    task = tasks.begin(thread, 'complete offline review')
                    tasks.add_acceptance(thread, 'human', 'inspect', kind='manual')
                    task = tasks.snapshot(thread)
                    base = {'branch_id': branch['id'], 'task_id': task['task_id'],
                            'expected_revision': task['revision']}
                    manual = await controller.command('work_control', {**base,
                        'operation': 'confirm_acceptance', 'acceptance_id': 'human', 'reason': 'reviewed'})
                    assert manual['task_id'] == task['task_id']
                    assert any(e['kind'] == 'manual' and e['status'] == 'passed' for e in manual['evidence']), manual
                    task = tasks.snapshot(thread)
                    sessions.append_event(thread, 'user', {'text': 'real offline source'})
                    node = controller.branches.commit_turn(branch['id'], 0, None, task_snapshot=task)
                    row = await controller.command('work_control', {**base,
                        'operation': 'feedback_stage', 'node_id': node['id'],
                        'context': 'offline', 'outcome': 'reviewed', 'behavior': 'inspect',
                        'boundary': 'same task', 'request_id': 'lazy-feedback'})
                    assert row['state'] == 'candidate', row
                    confirmed = await controller.command('work_control', {**base,
                        'operation': 'feedback_confirm', 'id': row['id'],
                        'expected_revision': row['revision']})
                    assert confirmed['state'] == 'confirmed', confirmed
                    assert controller.service is None
                    assert not controller.runs
                    print('LAZY_CONTROL_COMPLETE', flush=True)
                finally:
                    await controller.close()
            asyncio.run(run())
        """)
        with tempfile.TemporaryDirectory(prefix='ignovate-lazy-control-') as directory:
            try:
                result = subprocess.run([sys.executable, '-B', '-c', code,
                    str(Path(work_control.__file__).resolve().parents[2]), directory],
                    cwd=directory, capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired as error:
                self.fail(f'lazy controls blocked beyond 30s: {error.stdout!r} {error.stderr!r}')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('LAZY_CONTROL_COMPLETE', result.stdout)
