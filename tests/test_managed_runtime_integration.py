"""Local service, prepared model input and process integration contracts."""
import asyncio
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from agent import ReviewInputMiddleware, active_review_coverage, TaskLifecycleMiddleware, TaskExecutionStopped
from agent import AgentRuntimeFactory
from config import Settings
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from agent_service import AgentService
from nailong.core.permissions import PermissionEngine
from nailong.core.processes import execute_process
from nailong.core.review_evidence import ReviewCoverageCollector
from nailong.core.review_runtime import select_review_files
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
from nailong.tools.files import FileSession


class PreparedReviewAgent:
    def __init__(self, root, *, partial=False, previous=()):
        self.root = root
        self.partial = partial
        self.previous = list(previous)

    def get_state(self, config):
        return SimpleNamespace(values={'messages': self.previous}, metadata={'step': 0})

    async def astream(self, value, config, *, stream_mode):
        page = FileSession(self.root).read_file('source.py', max_chars=3 if self.partial else 1000)
        message = ToolMessage(content=json.dumps(page), name='read_file',
            tool_call_id='fresh-read', id='fresh-message')
        yield ('updates', {'tools': {'messages': [message]}})
        request = SimpleNamespace(messages=[*self.previous, message])
        await ReviewInputMiddleware().awrap_model_call(request,
            lambda prepared: self._respond(prepared))
        yield ('updates', {'model': {'messages': [AIMessage(content='已完成静态阅读。')]}})

    async def _respond(self, request):
        return SimpleNamespace(result=[AIMessage(content='已完成静态阅读。')])


class ManagedRuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.root = base / 'project'
        self.root.mkdir()
        (self.root / 'source.py').write_text('value = 1\n')
        self.sessions = ProjectSessionStore(self.root, base_dir=base / 'private')
        self.tasks = TaskStore(self.sessions)
        self.engine = PermissionEngine(self.root, rules={})
        self.agent = PreparedReviewAgent(self.root)
        factory = SimpleNamespace(settings=SimpleNamespace(project_root=self.root),
            task_store=self.tasks, session_store=self.sessions,
            async_runtime=lambda **options: self.agent)
        self.service = AgentService(factory, permission_engine=self.engine)
        self.config = {'configurable': {'thread_id': 'owner'}}

    async def test_resume_after_cancelled_approval_closes_old_tool_exchange(self):
        await self._assert_resume_cancelled_approval()

    async def test_resume_after_cancelled_approval_with_no_tools(self):
        await self._assert_resume_cancelled_approval(allowed_tools=frozenset())

    async def _assert_resume_cancelled_approval(self, *, allowed_tools=None):
        class Model(FakeMessagesListChatModel):
            def bind_tools(self, tools, **options): return self
        model = Model(responses=[AIMessage(content='', tool_calls=[{
            'name':'write_file', 'args':{'path':'a.txt', 'content':'a'}, 'id':'old-write'}]),
            AIMessage(content='已按当前要求继续')])
        settings = Settings('fake-key', 'https://example.invalid', 'private', self.root)
        with patch('agent.ChatDeepSeek', return_value=model):
            factory = AgentRuntimeFactory(settings, session_store=self.sessions)
        service = AgentService(factory)
        waiting = asyncio.Event()
        async def approval(*args):
            waiting.set()
            await asyncio.Future()
        async def first_turn():
            return [event async for event in service.stream_turn('创建 a.txt', self.config, approval_handler=approval)]
        try:
            first = asyncio.create_task(first_turn())
            await asyncio.wait_for(waiting.wait(), 5)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError): await first
            runtime = await factory.async_runtime(thread_id='owner')
            self.assertEqual((await runtime.aget_state(self.config)).next, ('tools',))
            self.assertEqual(factory.task_store.snapshot('owner')['lifecycle'], 'paused')
            events = [event async for event in service.stream_turn('继续执行', self.config,
                allowed_tools=allowed_tools,
                approval_handler=lambda *args:'approve_once')]
            self.assertEqual(events[-1].kind, 'final')
            self.assertFalse((self.root/'a.txt').exists())
            messages = (await runtime.aget_state(self.config)).values['messages']
            self.assertTrue(any(isinstance(m, ToolMessage) and m.tool_call_id=='old-write' for m in messages))
        finally:
            await factory.aclose()
            factory.close()

    async def test_full_review_uses_consumed_input_and_reports_reviewed(self):
        events = [event async for event in self.service.stream_turn('审查 source.py', self.config,
            profile='review', target_path='source.py')]
        final = events[-1]
        self.assertEqual(final.kind, 'final')
        self.assertEqual(final.data['delivery']['status'], 'reviewed')
        task = self.tasks.snapshot('owner')
        criterion = next(row for row in task['acceptance'] if row['id'] == 'review:scope')
        self.assertEqual(criterion['status'], 'passed')
        self.assertTrue(task['input_fingerprint'])
        evidence = next(row for row in task['evidence'] if row['kind'] == 'review')
        self.assertNotIn('value =', json.dumps(evidence))

    async def test_incomplete_model_input_cannot_pass_review(self):
        self.agent.partial = True
        events = [event async for event in self.service.stream_turn('审查 source.py', self.config,
            profile='review', target_path='source.py')]
        self.assertEqual(events[-1].data['delivery']['status'], 'unverified')
        self.assertFalse(any(row['kind'] == 'review' for row in self.tasks.snapshot('owner')['evidence']))

    async def test_new_file_during_enumeration_cannot_be_claimed_as_reviewed(self):
        task = await self.service._begin_task('owner', '审查目录', 'review', '.')
        def enumerate_then_add(*args, **kwargs):
            selection = select_review_files(*args, **kwargs)
            (self.root / 'new.py').write_text('not consumed by model\n')
            return selection
        with patch('nailong.core.review_runtime.select_review_files', side_effect=enumerate_then_add):
            collector = await self.service._prepare_review(self.agent, self.config, task)
        page = ToolMessage(content=json.dumps(FileSession(self.root).read_file('source.py')),
            name='read_file', tool_call_id='review-page')
        collector.consume_request([page])
        result = await self.service._finish_review('owner', task, collector)
        self.assertIsNone(result['evidence'])
        self.assertEqual(result['status'], 'unverified')
        self.assertFalse(any(row['kind'] == 'review' for row in self.tasks.snapshot('owner')['evidence']))

    async def test_historical_page_without_fresh_read_is_not_current_review(self):
        old = ToolMessage(content=json.dumps(FileSession(self.root).read_file('source.py')),
            name='read_file', tool_call_id='old-read', id='old-message')
        self.agent.previous = [old]
        task = await self.service._begin_task('owner', '审查', 'review', 'source.py')
        collector = await self.service._prepare_review(self.agent, self.config, task)
        collector.consume_request([old])
        result = await self.service._finish_review('owner', task, collector)
        self.assertIsNone(result['evidence'])
        self.assertEqual(self.tasks.snapshot('owner')['acceptance'][0]['status'], 'pending')

    async def test_old_turn_cancellation_does_not_pause_replacement_task(self):
        old = self.tasks.begin('owner', '修复 source.py')
        token = self.service._turn_task.set(old)
        try:
            replacement = self.tasks.begin('owner', '另一个任务', new=True)
            self.service._pause_turn_task('owner')
        finally:
            self.service._turn_task.reset(token)
        current = self.tasks.snapshot('owner')
        self.assertEqual(current['task_id'], replacement['task_id'])
        self.assertEqual(current['lifecycle'], 'active')

    async def test_old_turn_edit_observation_does_not_pollute_new_task(self):
        old = self.tasks.begin('owner', '修复 source.py')
        token = self.service._turn_task.set(old)
        try:
            self.tasks.begin('owner', '另一个任务', new=True)
            await self.service._observe_task_tool('owner', ToolMessage(
                content='{"ok":true,"path":"source.py"}', name='edit_file', tool_call_id='late'),
                {'path': 'source.py'})
        finally:
            self.service._turn_task.reset(token)
        self.assertEqual(self.tasks.snapshot('owner')['changed_paths'], [])
        self.assertEqual(self.tasks.snapshot('owner')['evidence'], [])

    async def test_delivery_after_requirement_change_does_not_use_old_identity(self):
        old = self.tasks.begin('owner', '修复 source.py')
        self.tasks.begin('owner', '另一个任务', new=True)
        self.assertIsNone(await self.service.delivery_report('owner', expected_task_id=old['task_id']))

    async def test_failed_model_handler_records_no_consumed_pages(self):
        collector = ReviewCoverageCollector(['source.py'], fingerprint_before='before')
        page = ToolMessage(content=json.dumps(FileSession(self.root).read_file('source.py')),
            name='read_file', tool_call_id='page')
        token = active_review_coverage.set(collector)
        async def fail(request):
            raise RuntimeError('provider failure')
        try:
            with self.assertRaisesRegex(RuntimeError, 'provider failure'):
                await ReviewInputMiddleware().awrap_model_call(SimpleNamespace(messages=[page]), fail)
        finally:
            active_review_coverage.reset(token)
        result = collector.finish(fingerprint_after='before', completed=True, task_revision=1,
            evidence_id='review')
        self.assertIsNone(result['evidence'])
        self.assertEqual(result['files'][0]['status'], 'unknown')

    async def test_async_process_retains_failure_summary_after_large_output(self):
        command = f"{sys.executable} -c \"print('BEGIN');print('x'*20000);print('FINAL_FAILURE');exit(3)\""
        result = await asyncio.wait_for(execute_process(command, self.root), timeout=5)
        self.assertFalse(result['ok'])
        self.assertEqual(result['exit_code'], 3)
        self.assertTrue(result['output_truncated'])
        self.assertIn('BEGIN', result['output'])
        self.assertIn('FINAL_FAILURE', result['output'])
        self.assertLessEqual(len(result['output']), 12000)

    async def test_bounded_selection_reports_permission_and_file_limit_gaps(self):
        self.engine.add_rule('deny', 'Read(source.py)')
        selection = select_review_files(self.root, ['.'], self.engine)
        self.assertFalse(selection['selection_complete'])
        self.assertEqual(selection['paths'], [])
        self.assertEqual(selection['skipped_paths'][0]['reason'], 'permission_denied')
        allowed = PermissionEngine(self.root, rules={})
        (self.root / 'other.py').write_text('value = 2\n')
        selection = select_review_files(self.root, ['.'], allowed, limit=1)
        self.assertFalse(selection['selection_complete'])
        self.assertEqual(len(selection['paths']), 1)

    async def test_parallel_graph_approvals_do_not_apply_first_decision_to_second_tool(self):
        class Model(FakeMessagesListChatModel):
            def bind_tools(self, tools, **options):
                return self
        model = Model(responses=[AIMessage(content='', tool_calls=[
            {'name': 'write_file', 'args': {'path': 'approved.txt', 'content': 'approved'}, 'id': 'approve'},
            {'name': 'write_file', 'args': {'path': 'rejected.txt', 'content': 'rejected'}, 'id': 'reject'},
        ]), AIMessage(content='批准项已执行，拒绝项未执行。')])
        settings = Settings('fake-key', 'https://api.deepseek.com', 'deepseek-chat', self.root)
        with patch('agent.ChatDeepSeek', return_value=model):
            factory = AgentRuntimeFactory(settings, session_store=self.sessions)
        decisions = []
        async def approve(action, index, total):
            decisions.append(action['args']['path'])
            return 'approve' if action['args']['path'] == 'approved.txt' else 'reject'
        try:
            service = AgentService(factory, permission_engine=self.engine)
            await service.run_turn('创建两个文件', self.config, approval_handler=approve)
            self.assertEqual(set(decisions), {'approved.txt', 'rejected.txt'})
            self.assertEqual((self.root / 'approved.txt').read_text(), 'approved')
            self.assertFalse((self.root / 'rejected.txt').exists())
        finally:
            await factory.aclose()
            factory.close()

    async def test_durable_paused_task_rejects_next_model_step(self):
        task = self.tasks.begin('owner', '调查 source.py')
        guard = TaskLifecycleMiddleware(self.tasks, task)
        self.tasks.set_state('owner', lifecycle='paused', blockers=['没有新证据'])
        with self.assertRaisesRegex(TaskExecutionStopped, '没有新证据'):
            await guard.abefore_model({}, None)
        self.tasks.begin('owner', '新的任务', new=True)
        with self.assertRaises(TaskExecutionStopped) as caught:
            await guard.abefore_model({}, None)
        self.assertTrue(caught.exception.task_replaced)

    async def test_repeated_read_stops_real_graph_before_seventh_model_call(self):
        class Model(FakeMessagesListChatModel):
            def bind_tools(self, tools, **options):
                return self
        responses = [AIMessage(content='', tool_calls=[{
            'name': 'read_file', 'args': {'path': 'source.py'}, 'id': f'read-{index}',
        }]) for index in range(6)] + [AIMessage(content='此调用不应发生')]
        model = Model(responses=responses)
        settings = Settings('fake-key', 'https://api.deepseek.com', 'deepseek-chat', self.root)
        with patch('agent.ChatDeepSeek', return_value=model):
            factory = AgentRuntimeFactory(settings, session_store=self.sessions)
        try:
            service = AgentService(factory, permission_engine=self.engine)
            events = [event async for event in service.stream_turn('排查 source.py', self.config)]
            self.assertEqual(model.i, 6)
            self.assertEqual(factory.task_store.snapshot('owner')['lifecycle'], 'paused')
            self.assertTrue(any(event.kind == 'task_paused' for event in events))
            self.assertEqual(events[-1].data['delivery']['status'], 'blocked')
        finally:
            await factory.aclose()
            factory.close()


if __name__ == '__main__':
    unittest.main()
