"""Native batch approvals must finish before any sibling starts a mutation.

The provider is offline; graph checkpoints, permissions, workers and writes are real.
"""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.sessions import ProjectSessionStore
from nailong.core.tool_approval import BatchApprovalMiddleware
from nailong.tools.files import FileSession


class OfflineModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class BatchApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='batch-approval-')
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.root = base / 'project'
        self.root.mkdir()
        self.store = ProjectSessionStore(self.root, base_dir=base / 'private')
        self.approvals = []
        self.effects_at_approval = []

    async def make_service(self, calls):
        model = OfflineModel(responses=[AIMessage(content='', tool_calls=calls),
            AIMessage(content='Delivered after tools.')])
        settings = Settings('offline-key', 'https://api.deepseek.com',
                            'deepseek-flash', self.root, reasoning_effort='max')
        with patch('agent.ChatDeepSeek', return_value=model):
            self.factory = AgentRuntimeFactory(settings, session_store=self.store)
        self.addAsyncCleanup(self.factory.aclose)
        self.addCleanup(self.factory.close)
        self.service = AgentService(self.factory, api_key='offline-key')

    async def approve(self, action, index, total):
        self.approvals.append((action['name'], action['args'].get('path')))
        self.effects_at_approval.append([(self.root / n).exists() for n in ['a.txt', 'b.txt']])
        return 'approve_once'

    async def drive(self, approval=None):
        return [event async for event in self.service.stream_turn(
            '现在授权实现：创建 a.txt 与 b.txt，保留实际修改证据。',
            {'configurable': {'thread_id': 'owner'}},
            approval_handler=approval or self.approve)]

    def calls(self):
        return [{'name': 'write_file', 'args': {'path': 'a.txt', 'content': 'alpha'}, 'id': 'a'},
                {'name': 'write_file', 'args': {'path': 'b.txt', 'content': 'beta'}, 'id': 'b'}]

    def results(self):
        agent = self.service._runtime('chat', None, 'owner')
        state = agent.get_state({'configurable': {'thread_id': 'owner'}})
        return {message.tool_call_id: json.loads(message.content)
                for message in state.values.get('messages', []) if isinstance(message, ToolMessage)}

    async def test_two_approved_creates_complete_without_replaying_started_write(self):
        # A sibling ASK must never cancel the first real atomic create.
        await self.make_service(self.calls())
        original = FileSession.write_file
        writes = []

        def slow_write(session, path, content, **kwargs):
            result = original(session, path, content, **kwargs)
            writes.append(path)
            time.sleep(.1)
            return result

        with patch.object(FileSession, 'write_file', slow_write):
            events = await self.drive()
        self.assertEqual(self.approvals, [('write_file', 'a.txt'), ('write_file', 'b.txt')])
        self.assertEqual(self.effects_at_approval, [[False, False], [False, False]])
        self.assertEqual((self.root / 'a.txt').read_text(), 'alpha')
        self.assertEqual((self.root / 'b.txt').read_text(), 'beta')
        self.assertCountEqual(writes, ['a.txt', 'b.txt'])
        self.assertFalse(any(e.kind == 'task_paused' for e in events))
        final = next(e for e in reversed(events) if e.kind == 'final')
        self.assertEqual(final.data['text'], 'Delivered after tools.')

    async def test_cancelling_second_approval_never_starts_first_write(self):
        # Cancelling the human review of a batch must leave both files absent.
        await self.make_service(self.calls())

        async def cancel_second(action, index, total):
            await self.approve(action, index, total)
            if action['args']['path'] == 'b.txt':
                raise asyncio.CancelledError()
            return 'approve_once'

        with self.assertRaises(asyncio.CancelledError):
            await self.drive(cancel_second)
        self.assertFalse((self.root / 'a.txt').exists())
        self.assertFalse((self.root / 'b.txt').exists())

    async def test_normal_command_with_schema_defaults_runs_after_approval(self):
        # Approved arguments must match the real adapter's inserted defaults.
        await self.make_service([{'name': 'run_command',
            'args': {'command': 'printf executed > ran.txt'}, 'id': 'command'}])
        events = await self.drive()
        self.assertEqual((self.root / 'ran.txt').read_text(), 'executed')
        results = [e for e in events if e.kind == 'tool_end']
        self.assertTrue(all(e.data['ok'] for e in results))

    async def test_missing_checkpoint_marker_never_reenters_per_tool_approval(self):
        await self.make_service(self.calls())
        original = BatchApprovalMiddleware._scope

        def lose_marker(middleware, request):
            request.state.pop('nailong_approval_batch', None)
            return original(middleware, request)

        with patch.object(BatchApprovalMiddleware, '_scope', lose_marker):
            await self.drive()
        self.assertEqual(self.approvals, [('write_file', 'a.txt'), ('write_file', 'b.txt')])
        self.assertEqual(self.effects_at_approval, [[False, False], [False, False]])
        for name in ('a', 'b'):
            self.assertFalse((self.root / (name + '.txt')).exists())
            self.assertEqual(self.results()[name]['error_code'], 'approval_required')
            self.assertFalse(self.results()[name]['started'])

    async def test_allowed_write_does_not_rebase_version_during_sibling_review(self):
        (self.root / 'existing.txt').write_text('original baseline')
        await self.make_service([
            {'name': 'write_file', 'args': {'path': 'existing.txt', 'content': 'model replacement'}, 'id': 'allowed'},
            {'name': 'write_file', 'args': {'path': 'b.txt', 'content': 'beta'}, 'id': 'b'}])
        session = self.factory._tool_session('owner')
        self.assertTrue(session.read_file('existing.txt')['ok'])
        self.service.permission_engine.grant_session('Write(./existing.txt)')

        async def external_change(action, index, total):
            await self.approve(action, index, total)
            (self.root / 'existing.txt').write_text('new UI baseline')
            self.assertTrue(session.read_file('existing.txt')['ok'])
            return 'approve_once'

        await self.drive(external_change)
        self.assertEqual((self.root / 'existing.txt').read_text(), 'new UI baseline')
        self.assertEqual((self.root / 'b.txt').read_text(), 'beta')
        self.assertFalse(self.results()['allowed']['ok'])
        self.assertFalse(self.results()['allowed']['started'])

    async def assert_unbound_approval_starts_nothing(self, *, missing):
        import agent_service
        await self.make_service(self.calls())
        bridge = agent_service._resume_approvals

        def stale_answer(actions, decisions):
            answer = bridge(actions, decisions)
            resume = {identity: dict(value) for identity, value in answer.resume.items()}
            for value in resume.values():
                if missing:
                    value.pop('_nailong_approval_batch')
                else:
                    value['_nailong_approval_batch'] = 'older-batch'
            return Command(resume=resume)

        with patch('agent_service._resume_approvals', stale_answer):
            await self.drive()
        for name in ('a', 'b'):
            self.assertFalse((self.root / (name + '.txt')).exists())
            self.assertEqual(self.results()[name]['error_code'], 'approval_rejected')
            self.assertFalse(self.results()[name]['started'])

    async def test_approval_with_correct_interrupt_but_no_batch_nonce_never_executes(self):
        await self.assert_unbound_approval_starts_nothing(missing=True)

    async def test_approval_with_correct_interrupt_but_stale_batch_nonce_never_executes(self):
        await self.assert_unbound_approval_starts_nothing(missing=False)


if __name__ == '__main__':
    unittest.main()
