"""Explicit full access must reach real tools without corrupting project evidence."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.permissions import PermissionEngine
from nailong.core.sessions import ProjectSessionStore
from nailong.core.delivery import build_delivery_report
from tests.test_managed_delivery import snapshot


class FakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class UnrestrictedServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.root = self.base / 'project'
        self.root.mkdir()
        self.external = self.base / 'external.py'
        self.external.write_text('value = 1\n', newline='\n')
        self.factories = []

    async def asyncTearDown(self):
        for factory in self.factories:
            await factory.aclose()
            factory.close()

    def service(self, responses, mode='bypassPermissions'):
        settings = Settings('test-secret', 'https://api.deepseek.com', 'deepseek-chat', self.root)
        store = ProjectSessionStore(self.root, base_dir=self.base / str(len(self.factories)))
        with patch('agent.ChatDeepSeek', return_value=FakeModel(responses=responses)):
            factory = AgentRuntimeFactory(settings, session_store=store)
        self.factories.append(factory)
        return AgentService(factory, api_key=settings.api_key,
            permission_engine=PermissionEngine(self.root), permission_mode=mode), factory

    async def test_default_review_rejects_external_path(self):
        service, _ = self.service([AIMessage(content='unused')], 'default')
        with self.assertRaisesRegex(ValueError, '项目根目录'):
            await service._runtime_async('review', str(self.external), 'review')

    def test_core_delivery_cannot_complete_external_change_from_project_proof(self):
        task = snapshot('test')
        task['acceptance'][0].update(status='passed', evidence_ids=['passed'])
        task['evidence'] = [{'id': 'passed', 'kind': 'test', 'source': 'runtime',
            'status': 'passed', 'coverage': 'complete', 'paths': ['a.py'],
            'task_revision': 2, 'input_fingerprint': 'input-v1', 'summary': '实际测试已通过'}]
        self.assertEqual(build_delivery_report(task, current_input_fingerprint='input-v1')['status'], 'verified')
        task['evidence'].append({'id': 'external', 'kind': 'edit', 'source': 'runtime',
            'status': 'passed', 'coverage': 'unknown', 'paths': [], 'task_revision': 1,
            'outside_project_scope': True})
        report = build_delivery_report(task, current_input_fingerprint='input-v1')
        self.assertEqual(report['status'], 'unverified')
        self.assertEqual(report['goal_coverage']['status'], 'unverified')

    async def test_full_review_consumes_external_file_and_preserves_paused_project_task(self):
        service, factory = self.service([
            AIMessage(content='', tool_calls=[{'name': 'read_file',
                'args': {'path': str(self.external)}, 'id': 'external-read'}]),
            AIMessage(content='读到了 value = 1。'),
        ])
        factory.task_store.begin('review', '修复项目文件')
        factory.task_store.set_state('review', lifecycle='paused', blockers=['等待用户'])
        before = factory.task_store._path('review').read_bytes()
        events = [row async for row in service.stream_turn('审查外部文件',
            {'configurable': {'thread_id': 'review'}}, profile='review',
            target_path=str(self.external), review_paths=frozenset({str(self.external.resolve())}))]
        result = next(row.data for row in events if row.kind == 'tool_end')
        self.assertTrue(result['ok'])
        self.assertEqual(factory.task_store._path('review').read_bytes(), before)
        self.assertIsNone(service.last_turn_task_id)
        self.assertFalse(any(row.kind == 'delivery' for row in events))

    async def test_external_edit_is_logged_and_does_not_reuse_project_verification(self):
        service, factory = self.service([
            AIMessage(content='', tool_calls=[{'name': 'read_file',
                'args': {'path': str(self.external)}, 'id': 'read'}]),
            AIMessage(content='', tool_calls=[{'name': 'edit_file',
                'args': {'path': str(self.external), 'old_string': 'value = 1', 'new_string': 'value = 2'},
                'id': 'edit'}]),
            AIMessage(content='外部文件已修改，未验证。'),
        ])
        events = [row async for row in service.stream_turn('修改指定外部文件',
            {'configurable': {'thread_id': 'edit'}}, allowed_tools={'read_file', 'edit_file'})]
        self.assertEqual(self.external.read_text(), 'value = 2\n')
        self.assertFalse(any(row.kind == 'approval_needed' for row in events))
        task = factory.task_store.snapshot('edit')
        self.assertTrue(any('验证范围外' in reason for reason in task['blockers']))
        self.assertTrue(any(row['kind'] == 'edit' and row['paths'] == [] for row in task['evidence']))
        self.assertTrue(any(row['kind'] == 'external_file_change'
            for row in factory.session_store.read_events('edit')))
        self.assertNotEqual((await service.delivery_report('edit'))['status'], 'verified')
        factory.task_store.reset_progress('edit')
        report = await service.delivery_report('edit')
        self.assertEqual(report['goal_coverage']['status'], 'unverified')
        self.assertTrue(any('项目验证范围外' in reason for reason in report['reasons']))

    async def test_full_review_reads_protected_file_without_project_task_registration(self):
        protected = self.root / '.env'
        protected.write_text('DEMO_VALUE=1\n', newline='\n')
        service, factory = self.service([
            AIMessage(content='', tool_calls=[{'name': 'read_file',
                'args': {'path': str(protected)}, 'id': 'read-protected'}]),
            AIMessage(content='只读检查完成。'),
        ])
        events = [row async for row in service.stream_turn('审查指定文件',
            {'configurable': {'thread_id': 'protected'}}, profile='review', target_path=str(protected))]
        self.assertTrue(next(row.data for row in events if row.kind == 'tool_end')['ok'])
        self.assertIsNone(factory.task_store.snapshot('protected'))
