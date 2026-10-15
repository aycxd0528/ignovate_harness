import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
from nailong.core.memory import MemoryStore
from nailong.core.feedback import FeedbackStore


class FeedbackExperienceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'project'
        self.project.mkdir()
        self.sessions = ProjectSessionStore(self.project, base_dir=Path(self.temp.name) / 'data')
        self.tasks = TaskStore(self.sessions)
        self.task = self.tasks.begin('thread1', '修复启动')
        self.memories = MemoryStore(self.project)
        self.feedback = FeedbackStore(self.sessions, memory_store=self.memories)

    def candidate(self):
        return self.feedback.propose(task=self.task, source_ref='session:thread1#node:e1',
            context='这个仓库的小范围修复', outcome='用户要求减少无关重构',
            behavior='保留现有接口，只修改故障路径', boundary='接口改造任务须重新判断', request_id='feedback1')

    def test_candidate_is_not_memory_until_confirmed_and_retries_are_stable(self):
        row = self.candidate()
        self.assertEqual(row['state'], 'candidate')
        self.assertEqual(row['id'], self.candidate()['id'])
        self.assertFalse(self.memories.document_path(row['document']).exists())
        confirmed = self.feedback.confirm(row['id'], task=self.task, expected_revision=row['revision'])
        memory = self.memories.read_document(confirmed['document'])
        self.assertTrue(memory['ok'])
        self.assertEqual(memory['knowledge_state'], 'confirmed')
        self.assertFalse(memory['fact_verified'])
        self.assertIn('保留现有接口', memory['content'])
        again = self.feedback.confirm(row['id'], task=self.task, expected_revision=row['revision'])
        self.assertEqual(again, confirmed)

    def test_new_task_revision_cannot_confirm_old_candidate(self):
        row = self.candidate()
        self.tasks.amend('thread1', objective='改造启动接口')
        with self.assertRaisesRegex(ValueError, '版本|修订|任务'):
            self.feedback.confirm(row['id'], task=self.tasks.snapshot('thread1'), expected_revision=row['revision'])
        self.assertFalse(self.memories.document_path(row['document']).exists())

    def test_revoked_memory_is_not_available_for_recall(self):
        row = self.candidate()
        confirmed = self.feedback.confirm(row['id'], task=self.task, expected_revision=row['revision'])
        retired = self.feedback.revoke(row['id'], expected_revision=confirmed['revision'])
        self.assertEqual(retired['state'], 'revoked')
        recalled = self.memories.read_document(row['document'])
        self.assertFalse(recalled['ok'])
        self.assertEqual(recalled['status'], 'revoked')
        self.assertEqual(self.feedback.revoke(row['id'], expected_revision=confirmed['revision']), retired)

    def test_confirmation_cannot_overwrite_user_edited_memory(self):
        row = self.candidate()
        path = self.memories.document_path(row['document'])
        path.parent.mkdir(parents=True)
        path.write_text('用户已有的独立内容')
        with self.assertRaises(ValueError):
            self.feedback.confirm(row['id'], task=self.task, expected_revision=row['revision'])
        self.assertEqual(path.read_text(), '用户已有的独立内容')

    def test_full_length_feedback_is_read_back_without_truncation(self):
        row = self.feedback.propose(task=self.task, source_ref='session:thread1#event:e1',
            context='情境' * 900, outcome='结果' * 900, behavior='行为' * 900,
            boundary='边界' * 900, request_id='long')
        confirmed = self.feedback.confirm(row['id'], task=self.task, expected_revision=1)
        self.assertEqual(confirmed['state'], 'confirmed')
        self.assertEqual(self.memories.read_document(row['document'], limit=16000)['content'],
                         self.feedback._content(row))

    def test_confirm_and_revoke_recover_after_memory_commit_before_receipt(self):
        import nailong.core.feedback as module
        row = self.candidate()
        real_write = module.atomic_json
        def fail_confirm(path, value, boundary):
            if value['state'] == 'confirmed':
                raise OSError('simulated crash after memory publication')
            return real_write(path, value, boundary)
        with patch.object(module, 'atomic_json', side_effect=fail_confirm):
            with self.assertRaises(OSError):
                self.feedback.confirm(row['id'], task=self.task, expected_revision=1)
        confirmed = self.feedback.confirm(row['id'], task=self.task, expected_revision=1)
        def fail_revoke(path, value, boundary):
            if value['state'] == 'revoked':
                raise OSError('simulated crash after revocation')
            return real_write(path, value, boundary)
        with patch.object(module, 'atomic_json', side_effect=fail_revoke):
            with self.assertRaises(OSError):
                self.feedback.revoke(row['id'], expected_revision=confirmed['revision'])
        retired = self.feedback.revoke(row['id'], expected_revision=confirmed['revision'])
        self.assertEqual(retired['state'], 'revoked')
        self.assertEqual(self.memories.read_document(row['document'])['status'], 'revoked')

    def test_revoke_also_removes_unsettled_confirmed_publication(self):
        row = self.candidate()
        self.memories.commit_document(self.memories.stage_document(row['document'], self.feedback._content(row)))
        retired = self.feedback.revoke(row['id'], expected_revision=1)
        self.assertEqual(retired['state'], 'revoked')
        self.assertFalse(self.memories.read_document(row['document'])['ok'])

    def test_revocation_removes_guidance_from_historical_memory_reads(self):
        import json
        from langchain_core.messages import ToolMessage
        from nailong.core.memory import load_project_memory
        from nailong.core.memory_context import MemoryReadContext
        row = self.candidate()
        confirmed = self.feedback.confirm(row['id'], task=self.task, expected_revision=1)
        context = MemoryReadContext(load_project_memory(self.project,
            user_file=Path(self.temp.name) / 'user.md'))
        old = context.read(row['document'])
        self.feedback.revoke(row['id'], expected_revision=confirmed['revision'])
        message = ToolMessage(content=json.dumps(old), name='memory_read', tool_call_id='old')
        current = json.loads(context.filter_messages([message])[0].content)
        self.assertEqual(current['status'], 'revoked')
        self.assertNotIn('content', current)
