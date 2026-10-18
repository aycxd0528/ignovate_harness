"""Ordinary delivery must use durable, current native execution proof."""
import copy
import asyncio
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_intake import TaskIntake, user_request
from test_adaptive_task_flow import OfflineModel, answer
from platform_fixtures import python_command


class NativeAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / 'project'
        self.root.mkdir()
        (self.root / 'value.txt').write_text('bad')
        home = base / 'home'
        home.mkdir()
        home_patch = patch('pathlib.Path.home', return_value=home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.factory = AgentRuntimeFactory(Settings('fixture-key', 'https://fixture.invalid', 'deepseek-flash', self.root),
            session_store=ProjectSessionStore(self.root, base_dir=base / 'data'))
        self.factory.model = OfflineModel(responses=[answer()])
        self.service = AgentService(self.factory)
        self.tasks = self.factory.task_store
        self.thread = 'ordinary'
        self.command = python_command("from pathlib import Path; assert Path('value.txt').read_text()=='ok'")
        self.criteria = [{'description': 'value.txt 的当前内容为 ok', 'step': 'value', 'paths': ['.'], 'requirement_ids': ['r000001']}]
        self.steps = [{'name': 'value', 'kind': 'test', 'command': self.command}]

    async def asyncTearDown(self):
        await self.factory.aclose()
        self.factory.close()

    def bind(self, revision=None, criteria=None, steps=None, text='请修复 value.txt 并验证结果'):
        task = self.tasks.snapshot(self.thread)
        token = user_request.set({'thread_id': self.thread, 'turn_id': 'live-user', 'text': text})
        try:
            handler = getattr(TaskIntake(self.tasks, self.thread), 'bind_acceptance', None)
            self.assertTrue(callable(handler), 'ordinary tasks need a native acceptance binding entry point')
            return handler(acceptance=self.criteria if criteria is None else criteria,
                verification=self.steps if steps is None else steps,
                expected_revision=task['revision'] if revision is None else revision)
        finally:
            user_request.reset(token)

    def begin(self):
        return self.tasks.begin(self.thread, '请修复 value.txt 并验证结果')

    async def turn(self, text='请修复 value.txt 并验证结果', approval='approve_once'):
        return [event async for event in self.service.stream_turn(text,
            {'configurable': {'thread_id': self.thread}}, approval_handler=lambda *args: approval)]

    def script(self, revision=1, fixed=True):
        return [answer('', [{'name': 'bind_task_acceptance', 'args': {
            'acceptance': self.criteria, 'verification': self.steps, 'expected_revision': revision}, 'id': 'bind'}]),
            answer('', [{'name': 'read_file', 'args': {'path': 'value.txt'}, 'id': 'read'}]),
            *([answer('', [{'name': 'write_file', 'args': {'path': 'value.txt', 'content': 'ok'}, 'id': 'write'}])] if fixed else []),
            answer('实现完成。')]

    async def test_normal_chat_binds_edits_executes_and_delivers_current_proof(self):
        self.factory.model.responses = self.script()
        events = await self.turn()
        final = [e.data for e in events if e.kind == 'final'][-1]
        self.assertEqual(final['delivery']['status'], 'verified', str(final))
        task = self.tasks.snapshot(self.thread)
        self.assertEqual((self.root / 'value.txt').read_text(), 'ok')
        self.assertFalse(self.factory.goal_store.latest())
        self.assertTrue(any(e.kind == 'verification_step' and e.data['started'] for e in events))
        self.assertTrue(any(e['kind'] == 'test' and e['source'] == 'runtime' and e['status'] == 'passed' for e in task['evidence']))
        self.assertTrue(any(c.get('verification_binding', {}).get('source') == 'agent' for c in task['acceptance']))

    async def test_model_completion_text_cannot_hide_a_failed_bound_check(self):
        self.factory.model.responses = self.script(fixed=False)
        events = await self.turn()
        final = [e.data for e in events if e.kind == 'final'][-1]
        self.assertEqual(final['delivery']['status'], 'failed', str(final))
        self.assertTrue(any(e.kind == 'verification_step' and e.data['exit_code'] != 0 for e in events))

    async def test_denied_validation_never_starts_process_or_proves_acceptance(self):
        self.steps[0]['command'] = python_command("from pathlib import Path; Path('ran.txt').touch()")
        self.factory.model.responses = self.script(fixed=False)
        events = await self.turn(approval='reject')
        self.assertFalse((self.root / 'ran.txt').exists())
        self.assertEqual([e.data for e in events if e.kind == 'final'][-1]['delivery']['status'], 'unverified')
        self.assertTrue(any(e.kind == 'verification_step' and not e.data['started'] for e in events))

    async def test_invalid_bindings_and_stale_revision_leave_authority_unchanged(self):
        original = self.begin()
        cases = [dict(revision=0), dict(criteria=[{**self.criteria[0], 'step': 'missing'}]),
            dict(criteria=[{**self.criteria[0], 'paths': ['../outside']}]),
            dict(criteria=[]), dict(text='只解释，不要实施')]
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(self.bind(**case)['ok'])
                self.assertEqual(self.tasks.snapshot(self.thread), original)

    async def test_binding_is_idempotent_preserves_prior_conditions_and_declares_authorship(self):
        self.begin()
        self.tasks.add_acceptance(self.thread, 'user:manual', '用户检查视觉效果', kind='manual')
        first = self.bind()
        self.assertTrue(first['ok'], str(first))
        task = self.tasks.snapshot(self.thread)
        self.assertEqual(self.bind()['task_revision'], first['task_revision'])
        self.assertEqual(self.tasks.snapshot(self.thread), task)
        self.assertIn('user:manual', [c['id'] for c in task['acceptance']])
        self.assertEqual(task['acceptance_plan']['source'], 'agent')
        changed = copy.deepcopy(self.steps)
        changed[0]['command'] = python_command('pass')
        self.assertFalse(self.bind(steps=changed)['ok'])
        self.assertEqual(self.tasks.snapshot(self.thread), task)

    async def test_active_legacy_goal_cannot_be_overridden_by_an_ordinary_plan(self):
        original = self.begin()
        self.factory.goal_store.create('持续修复 value.txt', thread_id=self.thread)
        self.assertFalse(self.bind()['ok'])
        self.assertEqual(self.tasks.snapshot(self.thread), original)

    async def test_explicit_read_tool_allowlist_cannot_run_bound_validation(self):
        self.begin()
        self.steps[0]['command'] = python_command("from pathlib import Path; Path('ran.txt').touch()")
        self.assertTrue(self.bind()['ok'])
        self.factory.model.responses = [answer('只读结束。')]
        events = [e async for e in self.service.stream_turn('继续',
            {'configurable': {'thread_id': self.thread}}, allowed_tools={'read_file'},
            approval_handler=lambda *args: 'approve_once')]
        self.assertFalse((self.root / 'ran.txt').exists())
        self.assertNotEqual([e.data for e in events if e.kind == 'final'][-1]['delivery']['status'], 'verified')

    async def test_delivery_and_context_preserve_agent_coverage_provenance(self):
        from nailong.core.delivery import render_delivery_report
        from nailong.core.task_context import validate_task_snapshot
        self.factory.model.responses = self.script()
        events = await self.turn()
        delivery = [e.data for e in events if e.kind == 'final'][-1]['delivery']
        condition = next(c for c in delivery['satisfied'] if c['id'].startswith('auto:'))
        self.assertEqual(condition.get('coverage_declaration'), {'source': 'agent', 'requirement_ids': ['r000001']})
        self.assertEqual(delivery['goal_coverage'].get('coverage_basis'), 'agent_declared')
        projected = validate_task_snapshot(self.tasks.snapshot(self.thread))
        condition = next(c for c in projected['acceptance'] if c['id'].startswith('auto:'))
        self.assertEqual(condition.get('requirement_ids'), ['r000001'])
        self.assertIn('代理声明', render_delivery_report(delivery))
        self.assertNotIn('用户目标覆盖：已验证', render_delivery_report(delivery))

    async def test_new_user_requirement_requires_binding_before_reusing_pass(self):
        self.factory.model.responses = self.script()
        first = await self.turn()
        self.assertEqual([e.data for e in first if e.kind == 'final'][-1]['delivery']['status'], 'verified')
        self.factory.model.responses = [answer('已有验证通过。')]
        events = await self.turn('请增加 value.txt 对中文内容的处理')
        self.assertEqual([e.data for e in events if e.kind == 'final'][-1]['delivery']['status'], 'unverified')
        self.assertFalse(any(e.kind == 'verification_step' for e in events))

    async def test_external_file_change_cannot_reuse_successful_evidence(self):
        self.factory.model.responses = self.script()
        first = await self.turn()
        self.assertEqual([e.data for e in first if e.kind == 'final'][-1]['delivery']['status'], 'verified')
        (self.root / 'value.txt').write_text('broken externally')
        self.factory.model.responses = [answer('继续核对。')]
        events = await self.turn('继续')
        final = [e.data for e in events if e.kind == 'final'][-1]
        self.assertEqual(final['delivery']['status'], 'failed', str(final))
        self.assertTrue(any(e.kind == 'verification_step' for e in events))

    async def test_unchanged_resume_reuses_exact_current_native_proof_without_process_replay(self):
        self.factory.model.responses = self.script()
        first = await self.turn()
        self.assertEqual([e.data for e in first if e.kind == 'final'][-1]['delivery']['status'], 'verified')
        self.factory.model.responses = [answer('交付已核对。')]
        events = await self.turn('继续')
        self.assertEqual([e.data for e in events if e.kind == 'final'][-1]['delivery']['status'], 'verified')
        self.assertFalse(any(e.kind == 'verification_step' for e in events), 'do not rerun an unchanged successful check')

    async def test_binding_must_explicitly_account_for_every_active_user_requirement(self):
        self.begin()
        self.tasks.begin(self.thread, '请增加中文支持')
        before = self.tasks.snapshot(self.thread)
        criteria = [{**self.criteria[0], 'requirement_ids': ['r000001']}]
        result = self.bind(criteria=criteria)
        self.assertFalse(result['ok'])
        self.assertEqual(self.tasks.snapshot(self.thread), before)
        criteria[0]['requirement_ids'].append('r000002')
        result = self.bind(criteria=criteria)
        self.assertTrue(result['ok'], str(result))
        condition = next(c for c in self.tasks.snapshot(self.thread)['acceptance'] if c['id'].startswith('auto:'))
        self.assertEqual(condition['requirement_ids'], ['r000001', 'r000002'])

    async def test_approval_revision_race_never_runs_or_verifies_old_task(self):
        self.steps[0]['command'] = python_command("from pathlib import Path; Path('ran.txt').touch()")
        self.factory.model.responses = self.script(fixed=False)
        def approve(*args):
            self.tasks.amend(self.thread, objective='请先检查，不再执行旧检查')
            return 'approve_once'
        events = [e async for e in self.service.stream_turn('请验证 value.txt',
            {'configurable': {'thread_id': self.thread}}, approval_handler=approve)]
        self.assertFalse((self.root / 'ran.txt').exists())
        self.assertTrue(any(e.kind == 'verification_step' and not e.data['started'] for e in events))
        self.assertNotEqual((await self.service.delivery_report(self.thread))['status'], 'verified')

    async def test_cancelling_automatic_verification_keeps_interrupted_evidence(self):
        marker = self.root / 'started.marker'
        self.steps[0].update(command=python_command(
            "from pathlib import Path; import time; Path('started.marker').touch(); time.sleep(30)"),
            timeout_seconds=30, generated_paths=['started.marker'])
        self.factory.model.responses = self.script(fixed=False)
        running = asyncio.create_task(self.turn())
        try:
            async with asyncio.timeout(5):
                while not marker.exists():
                    if running.done():
                        self.fail(f'Ended before validation process started: {await running}')
                    await asyncio.sleep(.01)
            running.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await running
        finally:
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
        task = self.tasks.snapshot(self.thread)
        self.assertEqual(task['lifecycle'], 'paused')
        self.assertTrue(any(e['kind'] == 'test' and e['status'] == 'interrupted' for e in task['evidence']))
        self.assertNotEqual((await self.service.delivery_report(self.thread))['status'], 'verified')

    async def test_completed_verification_steps_stream_before_later_steps_finish(self):
        marker = self.root / 'running.marker'
        self.steps = [dict(name='fast', kind='test', command=python_command('pass')),
            dict(name='value', kind='test', command=python_command(
                "from pathlib import Path; import time; Path('running.marker').touch(); time.sleep(30)"),
                timeout_seconds=30, generated_paths=['running.marker'])]
        self.factory.model.responses = self.script(fixed=False)
        observed = []
        async def consume():
            async for event in self.service.stream_turn('请验证 value.txt',
                    {'configurable': {'thread_id': self.thread}}, approval_handler=lambda *a: 'approve_once'):
                observed.append(event)
        running = asyncio.create_task(consume())
        try:
            async with asyncio.timeout(5):
                while not marker.exists():
                    if running.done():
                        self.fail(f'Ended before second process started: {await running}')
                    await asyncio.sleep(.01)
            self.assertTrue(any(e.kind == 'verification_step' and e.data['name'] == 'fast' for e in observed),
                'completed steps must be visible while later steps are still running')
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    async def test_transition_to_sustained_goal_preserves_and_verifies_ordinary_checks(self):
        from nailong.core.verification import VerificationService
        self.begin()
        self.assertTrue(self.bind()['ok'])
        (self.root / 'value.txt').write_text('ok')
        self.tasks.begin(self.thread, '请修复 value.txt 并持续推进，增加一项检查')
        intake = TaskIntake(self.tasks, self.thread)
        token = user_request.set({'thread_id': self.thread, 'turn_id': 'sustained-user', 'text': '请修复 value.txt 并持续推进，增加一项检查'})
        try:
            brief = {'objective': '持续验证 value.txt', 'scope': ['.'],
                'acceptance': [{'description': '追加检查通过', 'step': 'new', 'paths': ['.']}],
                'verification': [{'name': 'new', 'kind': 'test', 'command': python_command('pass')}]}
            prepared = intake.prepare(brief)
            self.assertTrue(prepared['ok'], str(prepared))
            self.assertTrue(intake.start(1)['ok'])
            intent = intake.consume()
        finally:
            user_request.reset(token)
        self.assertEqual({s['name'] for s in intent['verification']}, {'value', 'new'})
        verifier = VerificationService(self.root, self.factory.session_store, task_store=self.tasks,
            steps_override=intent['verification'])
        result = await verifier.run(self.thread, approval=lambda *args: 'approve_once')
        self.assertEqual(result['status'], 'passed')
        self.assertEqual((await self.service.delivery_report(self.thread))['status'], 'verified')
        self.assertTrue(all(c['status'] == 'passed' for c in self.tasks.snapshot(self.thread)['acceptance']))

    async def test_project_configured_failure_cannot_be_dropped_by_ordinary_binding(self):
        config = self.root / '.nailong' / 'settings.json'
        config.parent.mkdir()
        config.write_text(json.dumps({'verification': {'steps': [
            {'name': 'project-existing', 'kind': 'test', 'command': python_command('raise AssertionError("project check must run")')}]}}))
        self.factory.model.responses = self.script(revision=2)
        events = await self.turn()
        final = [e.data for e in events if e.kind == 'final'][-1]
        self.assertEqual((final.get('delivery') or {}).get('status'), 'failed', str(final))
        self.assertEqual({e.data['name'] for e in events if e.kind == 'verification_step'}, {'project-existing', 'value'})
        self.assertIn('verify:project-existing', [c['id'] for c in self.tasks.snapshot(self.thread)['acceptance']])

    async def test_new_project_checks_invalidate_reuse_and_are_required_when_rebinding(self):
        self.factory.model.responses = self.script()
        first = await self.turn()
        self.assertEqual([e.data for e in first if e.kind == 'final'][-1]['delivery']['status'], 'verified')
        config = self.root / '.nailong' / 'settings.json'
        config.parent.mkdir()
        config.write_text(json.dumps({'verification': {'steps': [
            {'name': 'added-check', 'kind': 'test', 'command': python_command('raise AssertionError("new required check")')}]}}))
        self.factory.model.responses = [answer('继续核对已有结果。')]
        events = await self.turn('继续')
        self.assertNotEqual([e.data for e in events if e.kind == 'final'][-1]['delivery']['status'], 'verified')
        task = self.tasks.snapshot(self.thread)
        self.factory.model.responses = self.script(revision=task['revision'], fixed=False)
        events = await self.turn('继续')
        self.assertEqual([e.data for e in events if e.kind == 'final'][-1]['delivery']['status'], 'failed')
        self.assertTrue(any(e.kind == 'verification_step' and e.data['name'] == 'added-check' for e in events))

    async def test_delivery_report_request_can_bind_current_checks_without_starting_another_goal(self):
        from nailong.core.task_requests import task_request_kind
        self.factory.model.responses = self.script()
        await self.turn()
        before = self.tasks.snapshot(self.thread)
        self.criteria[0]['requirement_ids'].append('r000002')
        self.factory.model.responses = self.script(revision=before['revision'] + 1, fixed=False)
        events = await self.turn('整理最终变更说明，列出已实际执行验证、命令和结果证据以及限制。')
        self.assertEqual([e.data for e in events if e.kind == 'final'][-1]['delivery']['status'], 'verified')
        for text in ['只汇报最终变更说明，不要运行测试', '整理最终变更说明，但禁止执行命令', '最终变更说明是什么？']:
            self.assertNotEqual(task_request_kind(text), 'report')

    async def test_report_no_execution_directives_override_preapproved_commands(self):
        texts = ['整理最终报告，别运行检查。', '整理最终变更说明，只需要汇报。',
            '整理最终变更说明，不要启动任何命令。', '整理最终变更说明，不必重新测试。',
            '整理最终变更说明，仅汇报，不要做任何操作。', '整理最终报告，先别执行任何操作。',
            '请修复 value.txt，但不要运行测试。', '请验证 value.txt，先别启动任何命令。']
        self.service.permission_engine.grant_session('Bash(*)')
        for index, text in enumerate(texts):
            with self.subTest(text=text):
                self.thread = 'no-run-' + str(index)
                self.criteria[0]['requirement_ids'] = ['r000001']
                self.steps = [{'name': 'value', 'kind': 'test', 'command': self.command}]
                self.factory.model.responses = self.script()
                self.factory.model.i = 0
                events = await self.turn()
                self.assertEqual([e.data for e in events if e.kind == 'final'][-1]['delivery']['status'], 'verified')
                before = self.tasks.snapshot(self.thread)
                marker = self.root / ('forbidden-' + str(index))
                self.steps.append({'name': 'new', 'kind': 'test', 'command': python_command(
                    f"from pathlib import Path; Path({marker.name!r}).touch()")})
                self.criteria[0]['requirement_ids'].append('r000002')
                self.factory.model.responses = self.script(revision=before['revision'] + 1, fixed=False)
                self.factory.model.i = 0
                events = await self.turn(text, approval=None)
                self.assertFalse(marker.exists())
                self.assertFalse(any(e.kind == 'verification_step' for e in events))

    async def test_model_declared_checks_cannot_bypass_pretool_command_hooks(self):
        config = self.root / '.nailong'
        config.mkdir()
        hook_command = python_command('raise SystemExit(2)')
        (config / 'settings.json').write_text(json.dumps({'hooks': {'PreToolUse': [
            {'matcher': '^run_command$', 'hooks': [{'type': 'command', 'command': hook_command}]}]}}))
        (config / 'settings.local.json').write_text(json.dumps({'approved_hook_hashes': [
            hashlib.sha256(hook_command.encode()).hexdigest()]}))
        self.assertTrue((await self.service._run_hook('PreToolUse', name='run_command')) .blocked)
        marker = self.root / 'blocked.marker'
        self.steps[0]['command'] = python_command("from pathlib import Path; Path('blocked.marker').touch()")
        self.factory.model.responses = self.script(fixed=False)
        events = await self.turn()
        self.assertFalse(marker.exists())
        self.assertNotEqual([e.data for e in events if e.kind == 'final'][-1]['delivery']['status'], 'verified')
        self.assertTrue(any(e.kind == 'hook_blocked' for e in events))

    async def test_cancel_during_post_hook_preserves_already_executed_command(self):
        config = self.root / '.nailong'
        config.mkdir()
        hook_command = python_command("from pathlib import Path; import time; Path('post.marker').touch(); time.sleep(30)")
        (config / 'settings.json').write_text(json.dumps({'hooks': {'PostToolUse': [
            {'matcher': '^run_command$', 'hooks': [{'type': 'command', 'command': hook_command}]}]}}))
        (config / 'settings.local.json').write_text(json.dumps({'approved_hook_hashes': [
            hashlib.sha256(hook_command.encode()).hexdigest()]}))
        self.steps[0].update(command=python_command("from pathlib import Path; Path('executed.marker').touch()"),
            generated_paths=['executed.marker', 'post.marker'])
        self.factory.model.responses = self.script(fixed=False)
        running = asyncio.create_task(self.turn())
        try:
            async with asyncio.timeout(5):
                while not (self.root / 'post.marker').exists():
                    if running.done():
                        self.fail(f'Ended before post hook started: {await running}')
                    await asyncio.sleep(.01)
            self.assertTrue((self.root / 'executed.marker').exists())
            running.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await running
        finally:
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
        records = [e['data'] for e in self.factory.session_store.read_events(self.thread) if e['kind'] == 'verification']
        self.assertEqual(records[-1]['status'], 'cancelled')
        self.assertEqual(len(records[-1]['steps']), 1)
        self.assertTrue(records[-1]['steps'][0]['started'])
        self.assertEqual(records[-1]['steps'][0]['exit_code'], 0)
        self.assertTrue(any(e['kind'] == 'test' for e in self.tasks.snapshot(self.thread)['evidence']))
        self.assertNotEqual((await self.service.delivery_report(self.thread))['status'], 'verified')

    async def test_model_prepared_goal_verification_obeys_the_same_command_hook(self):
        from nailong.core.verification import verify_goal_if_configured
        config = self.root / '.nailong'
        config.mkdir()
        hook_command = python_command('raise SystemExit(2)')
        (config / 'settings.json').write_text(json.dumps({'hooks': {'PreToolUse': [
            {'matcher': '^run_command$', 'hooks': [{'type': 'command', 'command': hook_command}]}]}}))
        (config / 'settings.local.json').write_text(json.dumps({'approved_hook_hashes': [hashlib.sha256(hook_command.encode()).hexdigest()]}))
        self.begin()
        intake = TaskIntake(self.tasks, self.thread)
        token = user_request.set({'thread_id': self.thread, 'turn_id': 'goal-user', 'text': '请修复 value.txt 并持续执行验证'})
        try:
            prepared = intake.prepare({'objective': '持续验证 value.txt', 'scope': ['.'],
                'acceptance': [{'description': '检查实际执行', 'step': 'probe', 'paths': ['.']}],
                'verification': [{'name': 'probe', 'kind': 'test', 'command': python_command(
                    "from pathlib import Path; Path('forbidden-goal.marker').touch()")}]})
            self.assertTrue(prepared['ok'])
            self.assertTrue(intake.start(1)['ok'])
            intent = intake.consume()
        finally:
            user_request.reset(token)
        goal = self.factory.goal_store.create(intent['objective'], thread_id=self.thread)
        record = await verify_goal_if_configured(self.service, self.factory, goal, self.thread, approval=lambda *a: 'approve_once')
        self.assertFalse((self.root / 'forbidden-goal.marker').exists())
        self.assertEqual(record['status'], 'unverified')
