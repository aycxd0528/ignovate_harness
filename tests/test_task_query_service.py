"""Read-only task questions must not amend or resume durable work."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent_service import AgentService
from nailong.core.permissions import PermissionEngine
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore


class TaskQueryServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.root = base / 'project'
        self.root.mkdir()
        (self.root / 'source.py').write_text('value = 1\n', newline='\n')
        self.sessions = ProjectSessionStore(self.root, base_dir=base / 'private')
        self.tasks = TaskStore(self.sessions)
        self.tasks.begin('owner', '修复 source.py', scope=['source.py'])
        self.tasks.add_acceptance('owner', 'behavior', '行为符合要求', kind='test')
        self.tasks.record_evidence('owner', {
            'id': 'test-result', 'kind': 'test', 'source': 'runtime', 'status': 'passed',
            'paths': ['source.py'], 'coverage': 'complete', 'summary': '定向测试通过',
            'input_fingerprint': 'recorded-fingerprint',
        }, acceptance_ids=['behavior'])
        self.tasks.set_step('owner', 'fix', title='修复实现', state='done')
        self.tasks.observe_progress('owner', {
            'tool': 'read_file', 'arguments_digest': 'args', 'result_digest': 'result',
            'input_version': 'version-1', 'ok': True, 'changed': False,
        })
        self.factory = SimpleNamespace(
            settings=SimpleNamespace(project_root=self.root),
            task_store=self.tasks, session_store=self.sessions,
            async_runtime=AsyncMock(side_effect=AssertionError('查询不能创建模型运行时')),
        )
        self.service = AgentService(self.factory, permission_engine=PermissionEngine(self.root))
        self.config = {'configurable': {'thread_id': 'owner'}}

    async def test_progress_questions_preserve_every_task_field_and_skip_runtime(self):
        for lifecycle in ('active', 'paused', 'blocked'):
            self.tasks.set_state('owner', lifecycle=lifecycle, blockers=['等待确认'])
            before = self.tasks.snapshot('owner')
            before_bytes = self.tasks._path('owner').read_bytes()
            for question in ('现在进展如何？', '进度怎么样', '当前任务状态', 'what is the task status?'):
                with self.subTest(lifecycle=lifecycle, question=question):
                    events = [event async for event in self.service.stream_turn(question, self.config)]
                    self.assertEqual(events[-1].kind, 'final')
                    self.assertIn('修复 source.py', events[-1].data['text'])
                    self.assertEqual(events[-1].data['stats']['model_calls'], 0)
                    self.assertEqual(self.tasks.snapshot('owner'), before)
                    self.assertEqual(self.tasks._path('owner').read_bytes(), before_bytes)
                    self.assertIsNone(self.service.last_turn_task_id)
        self.factory.async_runtime.assert_not_called()

    async def test_unrelated_chat_and_explanatory_question_do_not_resume_paused_work(self):
        for lifecycle in ('paused', 'blocked'):
            self.tasks.set_state('owner', lifecycle=lifecycle, blockers=['权限被拒绝'])
            before = self.tasks.snapshot('owner')
            for message in ('你好', '为什么测试失败？', '解释一下修改方案', '不要修改文件，先解释一下',
                    '修改方案是什么', '测试为什么失败', '不要恢复任务，修改方案先解释一下'):
                with self.subTest(lifecycle=lifecycle, message=message):
                    answer = await self.service.run_turn(message, self.config)
                    self.assertIn('/task resume', answer)
                    self.assertEqual(self.tasks.snapshot('owner'), before)
        self.factory.async_runtime.assert_not_called()

    async def test_direct_task_begin_is_read_only_for_queries_and_paused_chat(self):
        self.tasks.set_state('owner', lifecycle='paused', blockers=['等待用户'])
        before = self.tasks.snapshot('owner')
        for message in ('现在进展如何？', '你好', '为什么测试失败？'):
            await self.service._begin_task('owner', message, 'chat')
            self.assertEqual(self.tasks.snapshot('owner'), before)

    async def test_display_text_from_cli_still_uses_local_query_branch(self):
        before = self.tasks.snapshot('owner')
        events = [event async for event in self.service.stream_turn('现在进展如何？', self.config,
            history_display='现在进展如何？')]
        self.assertTrue(events[-1].data['local'])
        self.assertEqual(self.tasks.snapshot('owner'), before)
        self.factory.async_runtime.assert_not_called()

    async def test_rewind_after_query_only_removes_local_events(self):
        self.sessions.append_event('owner', 'turn_start', {'message_ids': ['prior-message'], 'visible': True})
        self.sessions.append_event('owner', 'final', {'text': '之前的真实工作回复'})
        old_events = self.sessions.read_events('owner')
        before = self.tasks.snapshot('owner')
        await self.service.run_turn('现在进展如何？', self.config, history_display='现在进展如何？')
        self.assertEqual(await self.service.rewind('owner'), 0)
        after_events = self.sessions.read_events('owner')
        self.assertEqual(after_events[:-1], old_events)
        self.assertEqual(after_events[-1]['kind'], 'rewind')
        self.assertTrue(after_events[-1]['data']['local'])
        self.assertEqual(self.tasks.snapshot('owner'), before)
        self.factory.async_runtime.assert_not_called()

    async def test_explicit_resume_keeps_requirements_and_evidence(self):
        self.tasks.set_state('owner', lifecycle='paused', blockers=['等待用户'])
        before = self.tasks.snapshot('owner')
        after = await self.service._begin_task('owner', '请继续执行', 'chat')
        self.assertEqual(after['lifecycle'], 'active')
        self.assertEqual(after['blockers'], [])
        for key in ('revision', 'latest_request', 'request_history', 'acceptance', 'evidence', 'objective'):
            self.assertEqual(after[key], before[key], key)

    async def test_actual_work_amends_and_resumes_task(self):
        self.tasks.set_state('owner', lifecycle='paused', blockers=['等待用户'])
        before = self.tasks.snapshot('owner')
        after = await self.service._begin_task('owner', '请修改 source.py，增加边界检查', 'chat')
        self.assertEqual(after['revision'], before['revision'] + 1)
        self.assertEqual(after['lifecycle'], 'active')
        self.assertEqual(after['latest_request'], '请修改 source.py，增加边界检查')
        self.assertEqual(after['acceptance'][0]['status'], 'stale')

    async def test_simple_project_explanation_is_not_a_requirement_change(self):
        before = self.tasks.snapshot('owner')
        self.assertIsNone(await self.service._begin_task('owner', '介绍一下这个项目的上下文处理', 'chat'))
        self.assertEqual(self.tasks.snapshot('owner'), before)

    async def test_polite_question_can_explicitly_request_work(self):
        self.tasks.set_state('owner', lifecycle='paused', blockers=['等待用户'])
        before = self.tasks.snapshot('owner')
        request = '请修改 source.py，增加边界检查，可以吗？'
        after = await self.service._begin_task('owner', request, 'chat')
        self.assertEqual(after['lifecycle'], 'active')
        self.assertEqual(after['revision'], before['revision'] + 1)
        self.assertEqual(after['latest_request'], request)

    async def test_resume_reconciles_file_changes_before_reusing_evidence(self):
        task = self.tasks.snapshot('owner')
        fingerprint = await self.service._task_input_fingerprint(task)
        self.tasks.record_verification('owner', {
            'thread_id': 'owner', 'cwd': str(self.tasks.project_root), 'task_id': task['task_id'],
            'task_revision': task['revision'], 'input_before': fingerprint, 'input_after': fingerprint,
            'status': 'passed', 'complete': True, 'steps': [],
        })
        self.tasks.set_state('owner', lifecycle='paused', blockers=['等待用户'])
        (self.root / 'source.py').write_text('value = 2\n', newline='\n')
        after = await self.service._begin_task('owner', '继续执行', 'chat')
        self.assertEqual(after['revision'], task['revision'])
        self.assertEqual(after['lifecycle'], 'active')
        self.assertEqual(after['acceptance'][0]['status'], 'stale')

    async def test_resume_syncs_changed_verification_configuration(self):
        import json
        self.tasks.set_state('owner', lifecycle='paused', blockers=['等待用户'])
        (self.root / '.nailong').mkdir()
        (self.root / '.nailong/settings.json').write_text(json.dumps({'verification': {'steps': [
            {'name': 'smoke', 'kind': 'test', 'command': 'python3 -c "pass"'}
        ]}}), newline='\n')
        after = await self.service._begin_task('owner', '继续执行', 'chat')
        self.assertEqual(after['lifecycle'], 'active')
        self.assertTrue(any(row['id'] == 'verify:smoke' for row in after['acceptance']))

    async def test_query_without_task_returns_local_answer(self):
        answer = await self.service.run_turn('现在进展如何？', {'configurable': {'thread_id': 'empty'}})
        self.assertIn('没有工程任务', answer)
        self.assertIsNone(self.tasks.snapshot('empty'))
        self.factory.async_runtime.assert_not_called()


class CliTaskQueryTests(unittest.TestCase):
    def test_cli_display_text_does_not_invoke_model_or_mutate_paused_task(self):
        import main
        from unittest.mock import patch
        from config import Settings

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            sessions = ProjectSessionStore(root, base_dir=root / 'private')
            tasks = TaskStore(sessions)
            tasks.begin('cli-owner', '修复功能')
            tasks.set_state('cli-owner', lifecycle='paused', blockers=['等待用户继续'])
            sessions.append_event('cli-owner', 'turn_start', {'message_ids': [], 'visible': True})
            runtime = SimpleNamespace()
            factory = SimpleNamespace(
                settings=Settings('test-key', 'https://api.deepseek.com', 'deepseek-chat', root),
                task_store=tasks, session_store=sessions, close=lambda: None,
                aclose=AsyncMock(),
                async_runtime=AsyncMock(side_effect=AssertionError('CLI查询不能调用模型')),
            )
            runtime._nailong_runtime_factory = factory
            before = tasks.snapshot('cli-owner')
            inputs = iter(['现在进展如何？', '/exit'])
            outputs = []
            with patch('main.create_agent_runtime', return_value=runtime):
                main.run_cli(factory.settings, input_fn=lambda _: next(inputs),
                    output_fn=outputs.append, resume_session='cli-owner')
            self.assertTrue(any('目标：修复功能' in line for line in outputs), outputs)
            self.assertFalse(any('请求失败' in line for line in outputs), outputs)
            self.assertEqual(tasks.snapshot('cli-owner'), before)
            factory.async_runtime.assert_not_called()
