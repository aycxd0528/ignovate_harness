"""Budget pressure may clear recoverable older results, never current inputs."""
import copy
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from langchain.agents.middleware import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import Tool

from config import Settings
from nailong.core.budgets import _input_bound
from nailong.core.context import UsageCalibration
from nailong.core.context_runtime import ContextManagerMiddleware
from nailong.core.cumulative_budget import SessionTokenBudget, active_session_token_budget
from nailong.core.history_archive import HistoryArchive
from nailong.core.sessions import ProjectSessionStore


class BudgetContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'project'
        self.root.mkdir()
        self.sessions = ProjectSessionStore(self.root, base_dir=Path(temporary.name)/'private')
        self.archive = HistoryArchive(self.sessions.root/'history')
        self.manager = ContextManagerMiddleware(Settings('key', 'https://example.invalid', 'private', self.root),
            [], 'Keep all constraints', 'chat', 'one', self.sessions, self.archive, {}, UsageCalibration())
        self.model = FakeMessagesListChatModel(responses=[AIMessage(content='unused')])
        self.budget = SessionTokenBudget(12000)
        self.addCleanup(self.budget.close)
        token = active_session_token_budget.set(self.budget)
        self.addCleanup(active_session_token_budget.reset, token)

    def request(self, *, recoverable=True, incomplete=False):
        messages = [HumanMessage(content='MUST preserve the current request', id='current'),
            AIMessage(content='old', additional_kwargs={'reasoning_content': 'required reasoning'},
                tool_calls=[{'name': 'read_file', 'id': 'old-read', 'args': {'path': 'large.py'}}]),
            ToolMessage(content='a'*30000, name='read_file', tool_call_id='old-read', id='old'),
            AIMessage(content='latest', tool_calls=[{'name': 'read_file', 'id': 'latest-read', 'args': {'path': 'current.py'}}])]
        if not incomplete:
            messages.append(ToolMessage(content='b'*2000, name='read_file', tool_call_id='latest-read', id='latest'))
        tools = [Tool(name='read_history_result', description='Recover archived results', func=str)] if recoverable else []
        return ModelRequest(model=self.model, tools=tools, messages=messages,
            system_message=SystemMessage(content='Keep all constraints'), model_settings={'max_tokens': 1000})

    def test_budget_pressure_makes_older_result_recoverable_without_mutating_checkpoint(self):
        request = self.request()
        before = copy.deepcopy(request.messages)
        self.assertGreater(_input_bound(request)+1000, 12000)
        prepared, report = self.manager._prepare_request(request)
        self.assertLessEqual(_input_bound(prepared)+1000, 12000)
        self.assertEqual(prepared.model_settings['max_tokens'], 1000)
        self.assertEqual(prepared.messages[-1].content, 'b'*2000)
        self.assertEqual(request.messages, before)
        self.assertEqual(prepared.messages[0].content, 'MUST preserve the current request')
        self.assertEqual(prepared.messages[1].additional_kwargs['reasoning_content'], 'required reasoning')
        cleared = prepared.messages[2]
        reference = cleared.additional_kwargs['nailong_tool_reference']
        offset, content = 0, ''
        while True:
            page = self.archive.read('one', reference, offset=offset, max_chars=4000)
            content += page['content']
            if page['next_offset'] is None:
                break
            offset = page['next_offset']
        self.assertEqual(content.count('a'*30000), 1)
        self.assertTrue(any(row['kind'] == 'budget_compact' for row in self.sessions.read_events('one')))

    def test_missing_recovery_tool_preserves_all_original_results(self):
        request = self.request(recoverable=False)
        prepared, report = self.manager._prepare_request(request)
        self.assertEqual(prepared.messages, request.messages)
        self.assertGreater(_input_bound(prepared)+1000, 12000)

    def test_budget_pressure_keeps_large_archived_tail_available_through_native_tool(self):
        import json
        from tools import build_tools
        from nailong.tools.files import FileSession
        from nailong.core.history_archive import MAX_ARCHIVE_BYTES
        request = self.request()
        original = next(message for message in request.messages if message.id == 'old')
        original.content = 'x' * (MAX_ARCHIVE_BYTES + 4000) + 'TOOL_TAIL'
        before = copy.deepcopy(request.messages)
        prepared, _ = self.manager._prepare_request(request)
        self.assertLessEqual(_input_bound(prepared)+1000, 12000)
        self.assertEqual(request.messages, before)
        cleared = next(message for message in prepared.messages if message.id == 'old')
        reference = cleared.additional_kwargs['nailong_tool_reference']
        native = next(tool for tool in build_tools(file_session=FileSession(self.root),
            history_archive=self.archive, thread_id='one') if tool.name == 'read_history_result')
        page = json.loads(native.invoke({'reference':reference,'offset':len(original.content)-9}))
        self.assertTrue(page['ok'], page); self.assertEqual(page['content'], 'TOOL_TAIL')
        self.assertFalse(page['archive_truncated']); self.assertIsNone(page['next_offset'])

    def test_unresolved_latest_call_is_preserved(self):
        request = self.request(incomplete=True)
        prepared, report = self.manager._prepare_request(request)
        self.assertEqual(prepared.messages[-1].tool_calls, request.messages[-1].tool_calls)
        self.assertEqual(prepared.messages[-1].content, 'latest')

    def test_truncated_archive_cannot_replace_an_original_result(self):
        from test_complete_history_archive import legacy_partial
        request = self.request()
        old=next(message for message in request.messages if message.id=='old')
        reference=legacy_partial(self.archive,'one',old,{'path':'large.py'})
        original_save=self.archive.save
        def save(thread,message,arguments=None):
            return reference if message.id=='old' else original_save(thread,message,arguments)
        with patch.object(self.archive,'save',side_effect=save):
            prepared, report = self.manager._prepare_request(request)
        self.assertEqual(prepared.messages, request.messages)
        self.assertGreater(_input_bound(prepared)+1000, 12000)
        event = next(row['data'] for row in self.sessions.read_events('one')
                     if row['kind'] == 'budget_compact')
        self.assertFalse(event['applied'])
        self.assertEqual(event['reason'], 'archive_truncated')
