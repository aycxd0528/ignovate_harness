import importlib
import json
from dataclasses import replace
import sys
import tempfile
import unittest
from pathlib import Path

from nailong.core.permissions import Decision
from nailong.tools.execution import ToolExecutionContext
from nailong.tools.files import FileSession
from tools import build_tools

SERVER = Path(__file__).parent / 'fixtures/mcp_server.py'


class MCPToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        from nailong.mcp.config import MCPConfigStore
        from nailong.mcp.client import MCPManager
        self.store = MCPConfigStore(self.root)
        self.store.add('test', {'transport': 'stdio', 'command': sys.executable, 'args': [str(SERVER)]})
        self.manager = MCPManager(self.store)
        self.addAsyncCleanup(self.manager.aclose)
        await self.manager.connect('test')
        self.session = FileSession(self.root)
        self.execution = ToolExecutionContext(self.root)

    def make_tools(self, **options):
        try:
            return {tool.name: tool for tool in build_tools(file_session=self.session,
                execution_context=self.execution, mcp_manager=self.manager, **options)}
        except TypeError:
            self.fail('MCP tools are not integrated into the execution registry')

    async def test_approval_rejection_prevents_remote_side_effect(self):
        tools = self.make_tools()
        target = self.root / 'effect.txt'
        self.execution.approval_handler = lambda *args: {'type': 'reject'}
        result = json.loads(await tools['mcp__test__touch'].ainvoke({'path': str(target)}))
        self.assertEqual(result['error_code'], 'approval_rejected')
        self.assertFalse(target.exists())

    async def test_approval_receives_args_and_remote_paths_remain_remote(self):
        approvals = []
        self.execution.approval_handler = lambda action, permission: approvals.append(action) or {'type': 'approve'}
        tool = self.make_tools()['mcp__test__echo']
        result = json.loads(await tool.ainvoke({'payload': {'data': [1, {'valid': True}]}, 'path': '/remote/outside'}))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['structured_content']['path'], '/remote/outside')
        self.assertEqual(approvals[0]['args']['payload'], {'data': [1, {'valid': True}]})
        self.assertEqual(self.execution.permission_engine.decide_action('read_file', {'path': '/outside'}).decision, Decision.DENY)

    async def test_invalid_json_schema_is_rejected_before_approval(self):
        approvals = []
        self.execution.approval_handler = lambda *args: approvals.append(args) or {'type': 'approve'}
        result = json.loads(await self.make_tools()['mcp__test__echo'].ainvoke({'payload': 'not-an-object'}))
        self.assertEqual(result['error_code'], 'invalid_parameters')
        self.assertFalse(approvals)

    async def test_remote_arguments_cannot_override_adapter_routing(self):
        self.execution.approval_handler = lambda *args: {'type': 'approve'}
        target = self.root / 'should-not-exist.txt'
        result = json.loads(await self.make_tools()['mcp__test__echo'].ainvoke(
            {'payload': {}, 'path': str(target), '_remote': 'touch'}))
        self.assertTrue(result['ok'], result)
        self.assertFalse(target.exists(), 'Extra remote arguments must not change the selected tool')

    async def test_remote_parameters_named_like_langchain_internals_are_preserved(self):
        self.execution.approval_handler = lambda *args: {'type': 'approve'}
        arguments = {'config': {'business': True}, 'run_manager': 'remote', 'callbacks': [1, 2]}
        result = json.loads(await self.make_tools()['mcp__test__reserved'].ainvoke(arguments))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['structured_content'], arguments)

    async def test_permissions_and_profiles_cannot_be_widened_by_remote_annotations(self):
        tool = self.make_tools()['mcp__test__echo']
        self.assertEqual(self.execution.permission_engine.decide_action(tool.name, {'payload': {}}, tool_spec=tool.spec).decision, Decision.ASK)
        self.execution.permission_engine.grant_session(tool.name + '(*)')
        self.assertTrue(json.loads(await tool.ainvoke({'payload': {}}))['ok'])
        for profile in ('init', 'review', 'plan', 'subagent'):
            self.assertFalse(any(name.startswith('mcp__') for name in self.make_tools(profile=profile, target_path='.')))
        self.execution.permission_mode = 'plan'
        self.assertEqual(json.loads(await tool.ainvoke({'payload': {}}))['error_code'], 'permission_denied')
        self.assertFalse(any(name.startswith('mcp__') for name in self.make_tools(allowed_tools={'read_file'})))

    async def test_mcp_permission_grants_preserve_case_sensitive_tool_identity(self):
        tool = self.make_tools()['mcp__test__echo']
        engine = self.execution.permission_engine
        engine.grant_session(tool.name + '(*)')
        other_name = 'mcp__test__Echo'
        other = replace(tool.spec, name=other_name, permission_key=other_name)
        self.assertEqual(engine.decide_action(other_name, {'payload': {}}, tool_spec=other).decision, Decision.ASK)

    async def test_stale_tool_after_disconnect_does_not_call_replacement_connection(self):
        tool = self.make_tools()['mcp__test__echo']
        self.execution.approval_handler = lambda *args: {'type': 'approve'}
        await self.manager.disconnect('test')
        await self.manager.connect('test')
        result = json.loads(await tool.ainvoke({'payload': {}}))
        self.assertEqual(result['error_code'], 'mcp_connection_changed')

    async def test_long_remote_names_have_stable_distinct_namespaces(self):
        try:
            name = importlib.import_module('nailong.mcp.tools').tool_name
        except ModuleNotFoundError:
            self.fail('MCP tool adapter is missing')
        first, second = name('test', 'x' * 100 + 'a'), name('test', 'x' * 100 + 'b')
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 64)
        self.assertEqual(first, name('test', 'x' * 100 + 'a'))
        self.assertNotEqual(name('a__b', 'c'), name('a', 'b__c'))
        self.assertLessEqual(len(name('a' + '_' * 31, 'echo')), 64)


class MCPRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_turn_discovers_approves_calls_and_receives_mcp_result(self):
        from agent import AgentRuntimeFactory
        from agent_service import AgentService
        from config import Settings
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage
        from nailong.core.sessions import ProjectSessionStore
        class Model(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as data:
            root = Path(directory).resolve()
            settings = Settings('provider-test', 'https://api.invalid', 'deepseek-flash', root)
            factory = AgentRuntimeFactory(settings, session_store=ProjectSessionStore(root, base_dir=data))
            self.addCleanup(factory.close)
            self.addAsyncCleanup(factory.aclose)
            factory.mcp_manager.store.add('test', {'transport': 'stdio', 'command': sys.executable, 'args': [str(SERVER)]})
            await factory.mcp_manager.connect('test')
            factory.model = Model(responses=[
                AIMessage(content='', tool_calls=[{'name': 'mcp__test__echo', 'args': {'payload': {'query': 'record'}}, 'id': 'mcp-call'}]),
                AIMessage(content='已读取外部服务结果。'),
            ])
            service = AgentService(factory, api_key=settings.api_key, session_store=factory.session_store)
            approvals = []
            def approve(action, *args):
                approvals.append(action)
                return 'approve'
            answer = await service.run_turn('使用已连接的 MCP 查询记录。',
                {'configurable': {'thread_id': 'mcp-agent-turn'}, 'recursion_limit': 40}, approval_handler=approve)
            self.assertEqual(answer, '已读取外部服务结果。')
            self.assertEqual(approvals[0]['name'], 'mcp__test__echo')
            runtime = await factory.async_runtime(thread_id='mcp-agent-turn')
            state = await runtime.aget_state({'configurable': {'thread_id': 'mcp-agent-turn'}})
            tool_messages = [message for message in state.values['messages'] if message.type == 'tool']
            self.assertEqual(json.loads(tool_messages[-1].content)['structured_content']['payload'], {'query': 'record'})

    async def test_factory_registers_and_accounts_for_connected_tools_then_closes(self):
        from agent import AgentRuntimeFactory
        from config import Settings
        from nailong.core.sessions import ProjectSessionStore
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as data:
            root = Path(directory).resolve()
            settings = Settings('provider-test', 'https://api.invalid', 'deepseek-flash', root)
            factory = AgentRuntimeFactory(settings, session_store=ProjectSessionStore(root, base_dir=data))
            self.addCleanup(factory.close)
            self.addAsyncCleanup(factory.aclose)
            manager = getattr(factory, 'mcp_manager', None)
            self.assertIsNotNone(manager, 'Runtime factory must own MCP connections')
            manager.store.add('test', {'transport': 'stdio', 'command': sys.executable, 'args': [str(SERVER)]})
            await manager.connect('test')
            runtime = await factory.async_runtime(thread_id='mcp-runtime')
            self.assertIn('mcp__test__echo', runtime._nailong_tool_specs)
            self.assertIn('mcp__test__echo', factory.context_parts('mcp-runtime')['tool_definitions'])
            await factory.aclose()
            self.assertEqual(manager.tools('test'), ())
