"""Same-file batches fail before review through a real offline tool graph."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent_service import _interrupt_actions_from_update, _resume_approvals
from nailong.core.tool_approval import BatchApprovalMiddleware
from nailong.tools.execution import ToolExecutionContext
from nailong.tools.files import FileSession
from tools import build_tools


class OfflineModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class BatchFileConflictTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='batch-file-conflict-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.session = FileSession(self.root)
        self.observations = []
        self.started_actions = []

        def unexpected_per_tool_approval(*args):
            raise AssertionError('Native approval must use the batch barrier')

        self.execution = ToolExecutionContext(self.root,
            approval_handler=unexpected_per_tool_approval,
            pre_tool_hook=lambda action: self.started_actions.append(action['name']),
            observer=self.observations.append,
            artifact_directory=self.root / 'private-results')
        self.execution._native_approval_handler = unexpected_per_tool_approval
        self.tools = build_tools(file_session=self.session, execution_context=self.execution,
            allowed_tools={'read_file', 'edit_file', 'write_file'})
        self.config = {'configurable': {'thread_id': self.id()}}

    def graph(self, calls, *, reads=()):
        responses = []
        if reads:
            responses.append(AIMessage(content='', tool_calls=list(reads)))
        responses.extend([AIMessage(content='', tool_calls=calls),
                          AIMessage(content='Finished after the file results.')])
        return create_agent(model=OfflineModel(responses=responses), tools=self.tools,
            middleware=[BatchApprovalMiddleware(self.execution, self.tools, 'chat', self.id())],
            checkpointer=InMemorySaver())

    @staticmethod
    def call(name, identity, **arguments):
        return {'name': name, 'args': arguments, 'id': identity}

    async def start(self, graph):
        return await graph.ainvoke({'messages': [HumanMessage(content=
            '现在授权修改这些文件；保留读后修改与审批保护。')]}, self.config)

    async def approve(self, graph, pending):
        actions = _interrupt_actions_from_update(pending)
        return await graph.ainvoke(_resume_approvals(actions,
            [{'type': 'approve'} for _ in actions]), self.config)

    def results(self, result):
        return {m.tool_call_id: json.loads(m.content) for m in result['messages']
                if isinstance(m, ToolMessage)}

    def assert_conflicts(self, result, *identities):
        messages = {m.tool_call_id: m for m in result['messages'] if isinstance(m, ToolMessage)}
        for identity in identities:
            with self.subTest(call=identity):
                payload = json.loads(messages[identity].content)
                self.assertEqual(payload.get('error_code'), 'batch_file_conflict')
                self.assertFalse(payload['started'])
                self.assertEqual(messages[identity].status, 'error')
                observation = next(o for o in self.observations if o['call_id'] == identity)
                self.assertFalse(observation['started'])

    async def test_same_file_edits_never_start_or_request_review_while_other_file_runs(self):
        # Removing the preflight permits one edit and leaves a partial change.
        original = 'alpha\nbeta\n'
        (self.root / 'same.txt').write_text(original)
        graph = self.graph([
            self.call('edit_file', 'first', path='same.txt', old_string='alpha', new_string='ALPHA'),
            self.call('edit_file', 'second', path='same.txt', old_string='beta', new_string='BETA'),
            self.call('write_file', 'other', path='other.txt', content='independent')],
            reads=[self.call('read_file', 'read', path='same.txt')])
        pending = await self.start(graph)
        actions = _interrupt_actions_from_update(pending)
        self.assertEqual([a['_nailong_tool_call_id'] for a in actions], ['other'])
        self.assertEqual((self.root / 'same.txt').read_text(), original)
        self.assertFalse((self.root / 'other.txt').exists())
        result = await self.approve(graph, pending)
        self.assert_conflicts(result, 'first', 'second')
        self.assertEqual((self.root / 'same.txt').read_text(), original)
        self.assertEqual((self.root / 'other.txt').read_text(), 'independent')
        self.assertNotIn('edit_file', self.started_actions)
        self.assertTrue(self.results(result)['other']['started'])

    async def test_canonical_aliases_group_mixed_write_and_edit(self):
        # Comparing raw path strings lets write/edit aliases partially mutate one file.
        (self.root / 'nested').mkdir()
        (self.root / 'same.txt').write_text('original\n')
        graph = self.graph([
            self.call('write_file', 'replace', path='nested/../same.txt', content='replacement\n'),
            self.call('edit_file', 'edit', path=str(self.root / 'same.txt'),
                      old_string='original', new_string='edited')],
            reads=[self.call('read_file', 'read', path='./same.txt')])
        result = await self.start(graph)
        self.assertFalse(result.get('__interrupt__'))
        self.assert_conflicts(result, 'replace', 'edit')
        self.assertEqual((self.root / 'same.txt').read_text(), 'original\n')
        self.assertEqual(self.started_actions, ['read_file'])

    async def test_duplicate_new_file_paths_fail_without_read_snapshot_or_review(self):
        # Missing destinations also conflict: approving both must never create the first.
        graph = self.graph([
            self.call('write_file', 'first', path='new.txt', content='one'),
            self.call('write_file', 'second', path='./new.txt', content='two')])
        result = await self.start(graph)
        self.assertFalse(result.get('__interrupt__'))
        self.assert_conflicts(result, 'first', 'second')
        self.assertFalse((self.root / 'new.txt').exists())
        self.assertEqual(self.started_actions, [])

    async def test_all_conflicts_fail_even_without_snapshot_and_unread_sibling_stays_protected(self):
        # Detecting conflicts only after version preparation misses unread group members.
        (self.root / 'same.txt').write_text('original')
        (self.root / 'unread.txt').write_text('untouched')
        graph = self.graph([
            self.call('edit_file', 'first', path='same.txt', old_string='original', new_string='one'),
            self.call('write_file', 'second', path='same.txt', content='two'),
            self.call('edit_file', 'unread', path='unread.txt', old_string='untouched', new_string='bad'),
            self.call('write_file', 'other', path='other.txt', content='independent')])
        pending = await self.start(graph)
        self.assertEqual([a['_nailong_tool_call_id'] for a in _interrupt_actions_from_update(pending)], ['other'])
        result = await self.approve(graph, pending)
        self.assert_conflicts(result, 'first', 'second')
        self.assertEqual(self.results(result)['unread']['error_code'], 'read_required')
        self.assertFalse(self.results(result)['unread']['started'])
        self.assertEqual((self.root / 'same.txt').read_text(), 'original')
        self.assertEqual((self.root / 'unread.txt').read_text(), 'untouched')
        self.assertEqual((self.root / 'other.txt').read_text(), 'independent')

    async def test_incomplete_snapshot_does_not_allow_independent_full_overwrite(self):
        # The preflight must preserve full-read checks for independent overwrite calls.
        (self.root / 'partial.txt').write_text('first\nsecond\n')
        graph = self.graph([
            self.call('write_file', 'overwrite', path='partial.txt', content='replacement'),
            self.call('write_file', 'other', path='other.txt', content='independent')],
            reads=[self.call('read_file', 'read', path='partial.txt', limit=1)])
        pending = await self.start(graph)
        self.assertEqual([a['_nailong_tool_call_id'] for a in _interrupt_actions_from_update(pending)], ['other'])
        result = await self.approve(graph, pending)
        self.assertEqual(self.results(result)['overwrite']['error_code'], 'read_required')
        self.assertFalse(self.results(result)['overwrite']['started'])
        self.assertEqual((self.root / 'partial.txt').read_text(), 'first\nsecond\n')
        self.assertEqual((self.root / 'other.txt').read_text(), 'independent')

    async def test_conflicts_preserve_external_change_guard_for_independent_resume(self):
        # Rebuilding a pending plan on resume would rebase the approved create.
        graph = self.graph([
            self.call('write_file', 'first', path='same.txt', content='one'),
            self.call('write_file', 'second', path='./same.txt', content='two'),
            self.call('write_file', 'other', path='other.txt', content='model')])
        pending = await self.start(graph)
        actions = _interrupt_actions_from_update(pending)
        self.assertEqual([a['_nailong_tool_call_id'] for a in actions], ['other'])
        (self.root / 'other.txt').write_text('external')
        self.assertTrue(self.session.read_file('other.txt')['ok'])
        result = await self.approve(graph, pending)
        self.assert_conflicts(result, 'first', 'second')
        self.assertFalse(self.results(result)['other']['started'])
        self.assertEqual(self.results(result)['other']['error_code'], 'approval_precondition')
        self.assertEqual((self.root / 'other.txt').read_text(), 'external')
        self.assertFalse((self.root / 'same.txt').exists())

    async def test_allowed_conflict_calls_do_not_bypass_preflight(self):
        # Applying the conflict check only to ASK calls permits ALLOW siblings to mutate.
        self.execution.permission_engine.grant_session('Write(./same.txt)')
        graph = self.graph([
            self.call('write_file', 'first', path='same.txt', content='one'),
            self.call('write_file', 'second', path='same.txt', content='two')])
        result = await self.start(graph)
        self.assertFalse(result.get('__interrupt__'))
        self.assert_conflicts(result, 'first', 'second')
        self.assertFalse((self.root / 'same.txt').exists())

    async def test_custom_same_named_tool_is_not_classified_as_native_file_write(self):
        # Name-only classification would reject unrelated dynamic tool semantics.
        def custom_write(path: str, content: str):
            (self.root / ('custom-' + content + '.txt')).write_text(path)
            return {'ok': True, 'custom_content': content}

        tool = next(t for t in self.tools if t.name == 'write_file')
        tool.spec = replace(tool.spec, handler=custom_write)
        graph = self.graph([
            self.call('write_file', 'first', path='virtual.txt', content='one'),
            self.call('write_file', 'second', path='virtual.txt', content='two')])
        pending = await self.start(graph)
        self.assertEqual([a['_nailong_tool_call_id'] for a in _interrupt_actions_from_update(pending)], ['first', 'second'])
        result = await self.approve(graph, pending)
        self.assertTrue(self.results(result)['first']['ok'])
        self.assertTrue(self.results(result)['second']['ok'])
        self.assertEqual((self.root / 'custom-one.txt').read_text(), 'virtual.txt')
        self.assertEqual((self.root / 'custom-two.txt').read_text(), 'virtual.txt')
        self.assertFalse((self.root / 'virtual.txt').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
