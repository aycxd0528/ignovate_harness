"""Offline MCP regressions through the actual native graph and execution gate.

The provider and remote transport are deterministic in-memory fixtures. Tool
schemas, adapters, batch middleware, graph checkpoints and ToolMessages are real.
No network, product changes, candidate writes or scanner execution are involved.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent_service import _interrupt_actions_from_update, _resume_approvals
from nailong.core.tool_approval import BatchApprovalMiddleware
from nailong.mcp.tools import build_mcp_tools
from nailong.tools.execution import ToolExecutionContext
from nailong.tools.files import FileSession


class OfflineModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class OfflineRemote:
    """Only the transport is fake; all argument handling uses MCPExecutionTool."""
    def __init__(self):
        self.owner = object()
        self.calls = []

    def tools(self):
        return [('offline', SimpleNamespace(name='echo', description='offline echo',
            inputSchema={'type': 'object', 'properties': {'payload': {'type': 'object'}},
                         'required': ['payload']}))]

    def connection_identity(self, server):
        return self.owner

    async def call_tool(self, server, remote, arguments):
        self.calls.append((server, remote, arguments))
        return {'ok': True, 'structured_content': arguments}


class NativeMCPBoundaries(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='v4-independent-mcp-')
        self.addCleanup(self.directory.cleanup)
        self.remote = OfflineRemote()
        self.execution = ToolExecutionContext(self.directory.name)
        self.observations = []
        self.execution.observer = self.observations.append

        def unexpected_per_tool_approval(*args):
            raise AssertionError('The native batch must not fall back to per-tool approval')

        self.execution.approval_handler = unexpected_per_tool_approval
        self.execution._native_approval_handler = unexpected_per_tool_approval
        self.tools = build_mcp_tools(self.remote, self.execution, FileSession(self.directory.name))
        self.config = {'configurable': {'thread_id': self.id()}}

    def graph(self, calls):
        model = OfflineModel(responses=[AIMessage(content='', tool_calls=calls),
                                       AIMessage(content='Recovered after tool results')])
        return create_agent(model=model, tools=self.tools,
            middleware=[BatchApprovalMiddleware(self.execution, self.tools, 'chat', self.id())],
            checkpointer=InMemorySaver())

    def call(self, arguments, identity):
        return {'name': self.tools[0].name, 'args': arguments, 'id': identity}

    async def test_invalid_mcp_schema_is_a_recoverable_tool_message(self):
        graph = self.graph([self.call({'payload': 'invalid-object'}, 'invalid')])
        result = await graph.ainvoke({'messages': [HumanMessage(content='use the tool')]}, self.config)
        self.assertFalse(result.get('__interrupt__'))
        messages = [m for m in result['messages'] if isinstance(m, ToolMessage)]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].status, 'error')
        payload = json.loads(messages[0].content)
        self.assertEqual(payload['error_code'], 'invalid_parameters')
        self.assertFalse(payload['started'])
        self.assertEqual(result['messages'][-1].content, 'Recovered after tool results')
        self.assertEqual(self.remote.calls, [])
        self.assertEqual(len(self.observations), 1)
        self.assertFalse(self.observations[0]['started'])

    async def test_valid_mcp_preserves_business_keys_and_starts_after_batch_approval(self):
        arguments = {'payload': {'value': 7}, 'config': {'business': True},
                     'run_manager': 'remote', 'callbacks': [1, 2]}
        graph = self.graph([self.call(arguments, 'valid')])
        pending = await graph.ainvoke({'messages': [HumanMessage(content='use the tool')]}, self.config)
        actions = _interrupt_actions_from_update(pending)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['args'], arguments)
        self.assertEqual(self.remote.calls, [])
        self.assertEqual(self.observations, [])
        result = await graph.ainvoke(_resume_approvals(actions, [{'type': 'approve'}]), self.config)
        self.assertEqual(self.remote.calls, [('offline', 'echo', arguments)])
        message = next(m for m in result['messages'] if isinstance(m, ToolMessage))
        self.assertEqual(json.loads(message.content)['structured_content'], arguments)
        self.assertEqual(message.status, 'success')
        self.assertEqual(len(self.observations), 1)
        self.assertTrue(self.observations[0]['started'])

    async def test_mixed_invalid_and_valid_mcp_batch_keeps_each_tool_result(self):
        graph = self.graph([self.call({'payload': 'invalid-object'}, 'invalid'),
                            self.call({'payload': {'valid': True}}, 'valid')])
        pending = await graph.ainvoke({'messages': [HumanMessage(content='use both tools')]}, self.config)
        actions = _interrupt_actions_from_update(pending)
        self.assertEqual([a['_nailong_tool_call_id'] for a in actions], ['valid'])
        self.assertEqual(self.remote.calls, [])
        self.assertEqual(self.observations, [])
        result = await graph.ainvoke(_resume_approvals(actions, [{'type': 'approve'}]), self.config)
        messages = {m.tool_call_id: m for m in result['messages'] if isinstance(m, ToolMessage)}
        self.assertEqual(set(messages), {'invalid', 'valid'})
        self.assertEqual(json.loads(messages['invalid'].content)['error_code'], 'invalid_parameters')
        self.assertEqual(messages['invalid'].status, 'error')
        self.assertEqual(messages['valid'].status, 'success')
        self.assertEqual(len(self.remote.calls), 1)
        self.assertEqual(result['messages'][-1].content, 'Recovered after tool results')


if __name__ == '__main__':
    unittest.main(verbosity=2)
