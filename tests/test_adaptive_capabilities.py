"""Adaptive capabilities must change real requests without granting guessed tools."""
import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from pydantic import PrivateAttr
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.sessions import ProjectSessionStore


def response(text='收到。', calls=()):
    return AIMessage(content=text, tool_calls=list(calls), usage_metadata={
        'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110})


def call(name, arguments, identity):
    return {'name': name, 'args': arguments, 'id': identity, 'type': 'tool_call'}


class RequestProbe(FakeMessagesListChatModel):
    _bindings: list = PrivateAttr(default_factory=list)
    _requests: list = PrivateAttr(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self._bindings.append([tool.name for tool in tools])
        return self.bind(**kwargs)

    async def _agenerate(self, messages, **kwargs):
        self._requests.append(list(messages))
        return await super()._agenerate(messages, **kwargs)


class AdaptiveCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'project'
        self.root.mkdir()
        (self.root / 'README.md').write_text('Fixture project: no greeting needs this file.\n')
        self.home = Path(self.temporary.name) / 'home'
        self.home.mkdir()
        self.home_patch = patch('pathlib.Path.home', return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        for index in range(100):
            directory = self.root / '.agents' / 'skills' / f'fixture-{index}'
            directory.mkdir(parents=True)
            (directory / 'SKILL.md').write_text(
                f'---\nname: fixture-{index}\ndescription: '+ '用于一个独立领域的项目分析。' * 6
                + '\n---\nBODY_MUST_BE_LOADED_EXPLICITLY\n')
        self.factory = AgentRuntimeFactory(
            Settings('fixture-key', 'https://fixture.invalid', 'private-fixture', self.root),
            session_store=ProjectSessionStore(self.root, base_dir=Path(self.temporary.name) / 'data'))
        self.model = RequestProbe(responses=[response()])
        self.factory.model = self.model
        self.service = AgentService(self.factory)

    async def asyncTearDown(self):
        await self.factory.aclose()
        self.factory.close()

    async def ask(self, text, thread='chat', **kwargs):
        return await self.service.run_turn(text, {'configurable': {'thread_id': thread}}, **kwargs)

    def request_reports(self, thread='chat'):
        return [row['data'] for row in self.factory.session_store.read_events(thread)
                if row['kind'] == 'context_request']

    async def test_greeting_has_no_tools_or_skill_catalog_and_small_fixed_input(self):
        await self.ask('你好')
        self.assertEqual(self.model._bindings[-1] if self.model._bindings else [], [])
        report = self.request_reports()[-1]
        self.assertEqual(report['tools'], [])
        self.assertEqual(report['categories']['skill_catalog']['characters'], 0)
        fixed = sum(report['categories'][name]['raw_tokens']
                    for name in ('base_system', 'fixed_memory', 'skill_catalog', 'tool_definitions'))
        self.assertLess(fixed, 1500)
        self.assertEqual(len(self.model._requests), 1)

    async def test_greeting_fixed_input_drops_at_least_75_percent_from_full_request(self):
        from langchain_core.messages import HumanMessage, SystemMessage
        from nailong.core.context import request_report
        from tools import build_tools
        tools = build_tools(file_session=self.factory._tool_session('baseline'),
                            skill_registry=self.factory.skill_registry)
        parts = self.factory._prompt_parts('chat', None, {tool.name for tool in tools}, None)
        baseline = request_report([HumanMessage(content='你好')],
            system_message=SystemMessage(content=''.join(parts.values())), tools=tools, parts=parts)
        before = sum(baseline['categories'][name]['raw_tokens']
                     for name in ('base_system', 'fixed_memory', 'skill_catalog', 'tool_definitions'))
        await self.ask('你好')
        after = sum(self.request_reports()[-1]['categories'][name]['raw_tokens']
                    for name in ('base_system', 'fixed_memory', 'skill_catalog', 'tool_definitions'))
        self.assertLessEqual(after, before * .25)

    async def test_general_dialogue_uses_bounded_discovery_without_unrelated_skills(self):
        await self.ask('想和你讨论一个想法')
        self.assertEqual(set(self.model._bindings[-1]), {'discover_tools', 'search_skills'})
        report = self.request_reports()[-1]
        self.assertEqual(report['categories']['skill_catalog']['characters'], 0)
        self.assertLess(report['raw_estimated_tokens'], 2000)
        sent = '\n'.join(str(message.content) for message in self.model._requests[-1])
        self.assertNotIn('fixture-99', sent)
        self.assertNotIn('BODY_MUST_BE_LOADED_EXPLICITLY', sent)

    async def test_ordinary_task_with_many_evidence_records_uses_recoverable_projection(self):
        from nailong.core.task_context import render_task_context, TaskContextTooLargeError
        from nailong.core.task_history import TaskContextHistory
        tasks=self.factory.task_store
        tasks.begin('chat','只读取项目，保留原接口与所有原始证据。')
        for index in range(45):
            tasks.record_evidence('chat',{'id':f'read-{index:032d}','kind':'read','source':'runtime',
                'status':'passed','coverage':'complete','paths':['README.md'],
                'artifact_ref':'hist_'+f'{index:064x}','summary':f'原始读取证据 {index}'})
        with self.assertRaises(TaskContextTooLargeError):
            render_task_context(tasks.snapshot('chat'))
        await self.ask('继续只读调查并说明已经核对的事实。')
        self.assertIn('read_task_context',self.model._bindings[-1])
        report=self.request_reports()[-1]['task_projection']
        self.assertLessEqual(report['characters'],12000)
        history=TaskContextHistory(self.factory.history_archive,'chat',self.root)
        record=history.read(report['history_reference'],section='evidence',record_id='read-'+f'{0:032d}')
        self.assertTrue(record['archive_complete'])
        self.assertEqual(json.loads(record['content'])[0]['summary'],'原始读取证据 0')
        self.assertEqual(len(tasks.snapshot('chat')['evidence']),45)

    def test_common_chinese_execution_requests_are_work_but_questions_are_not(self):
        from nailong.core.task_requests import task_request_kind
        for text in ('请反复写文件', '依次写文件后汇报', '做两件事'):
            with self.subTest(text=text):
                self.assertEqual(task_request_kind(text), 'work')
        for text in ('如何写文件', '写文件的方案是什么', '只解释如何写文件'):
            with self.subTest(text=text):
                self.assertEqual(task_request_kind(text), 'other')

    async def test_discovery_loads_read_tool_on_next_model_request(self):
        self.model.responses = [
            response('', [call('discover_tools', {'query': 'read_file'}, 'discover')]),
            response('', [call('read_file', {'path': 'README.md'}, 'read')]), response('已读取。')]
        await self.ask('先讨论一下我们可能需要哪些资料')
        self.assertNotIn('read_file', self.model._bindings[0])
        self.assertIn('read_file', self.model._bindings[1])
        result = next(message for message in self.model._requests[-1]
                      if isinstance(message, ToolMessage) and message.tool_call_id == 'read')
        self.assertIn('Fixture project', str(result.content))
        self.assertEqual(self.request_reports()[0]['tools'], ['discover_tools', 'search_skills'])

    async def test_same_batch_discovery_does_not_authorize_guessed_write_or_ask_approval(self):
        approvals = []
        self.model.responses = [response('', [
            call('discover_tools', {'query': 'write_file'}, 'discover'),
            call('write_file', {'path': 'unrequested.txt', 'content': 'bad'}, 'guess')]), response()]
        await self.ask('先讨论一下', approval_handler=lambda action, *args: approvals.append(action) or 'approve_once')
        self.assertFalse((self.root / 'unrequested.txt').exists())
        self.assertEqual(approvals, [])
        failure = next(message for message in self.model._requests[-1]
                       if isinstance(message, ToolMessage) and message.tool_call_id == 'guess')
        self.assertEqual(json.loads(failure.content)['error_code'], 'tool_not_loaded')

    async def test_new_user_turn_resets_unrelated_loaded_tools(self):
        self.model.responses = [response('', [call('discover_tools', {'query': 'read_file'}, 'd')]), response()]
        await self.ask('先讨论一下')
        self.model.responses = [response()]
        self.model.i = 0
        await self.ask('再聊聊一个想法')
        self.assertEqual(set(self.model._bindings[-1]), {'discover_tools', 'search_skills'})

    async def test_explicit_allowed_tools_cannot_be_widened_by_discovery(self):
        self.model.responses = [response('', [call('read_file', {'path': 'README.md'}, 'read')]), response()]
        await self.ask('读取 README.md', allowed_tools={'read_file'})
        self.assertTrue(all(set(names) == {'read_file'} for names in self.model._bindings))
        result = next(message for message in self.model._requests[-1]
                      if isinstance(message, ToolMessage) and message.tool_call_id == 'read')
        self.assertIn('Fixture project', result.content)

    async def test_skill_search_returns_only_matching_metadata_without_body(self):
        self.model.responses = [response('', [call('search_skills', {'query': 'fixture-50'}, 'skills')]), response()]
        await self.ask('先聊聊一个想法')
        result = next(message for message in self.model._requests[-1]
                      if isinstance(message, ToolMessage) and message.tool_call_id == 'skills')
        data = json.loads(result.content)
        self.assertTrue(data['ok'])
        self.assertEqual([item['name'] for item in data['skills']], ['fixture-50'])
        self.assertNotIn('BODY_MUST_BE_LOADED_EXPLICITLY', result.content)

    async def test_greeting_preserves_paused_task_without_resuming_it(self):
        task = self.factory.task_store.begin('chat', '修复现有缺陷')
        self.factory.task_store.set_state('chat', lifecycle='paused', blockers=['用户暂停'])
        await self.ask('你好')
        current = self.factory.task_store.snapshot('chat')
        self.assertEqual(current['task_id'], task['task_id'])
        self.assertEqual(current['lifecycle'], 'paused')
        self.assertEqual(self.request_reports(), [])
        self.assertEqual(self.model._requests, [])

    def test_discovery_rejects_oversized_schema_instead_of_loading_truncated_contract(self):
        from langchain_core.tools import StructuredTool
        from nailong.core.capabilities import CapabilityCatalog
        def probe(value: str) -> dict:
            return {'ok': True}
        tool = StructuredTool.from_function(probe, name='huge_schema', description='长' * 20_000)
        catalog = CapabilityCatalog()
        catalog.register([tool])
        result = catalog.search('huge_schema')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_code'], 'tool_schema_too_large')
        self.assertNotIn('tools', result)

    def test_stale_catalog_never_enters_native_approval_preparation(self):
        from nailong.core.capabilities import BOUND, CapabilityCatalog
        from nailong.core.tool_approval import BatchApprovalMiddleware
        from tools import build_tools
        catalog = CapabilityCatalog()
        tools = build_tools(file_session=self.factory._tool_session('chat'),
                            execution_context=self.factory.tool_execution_context,
                            capability_catalog=catalog)
        catalog.register(tools)
        message = response('', [call('write_file', {'path': 'stale.txt', 'content': 'bad'}, 'stale')])
        message.response_metadata[BOUND] = {'catalog': 'old', 'names': ['write_file']}
        batch = BatchApprovalMiddleware(self.factory.tool_execution_context, tools, 'chat', 'chat',
                                        capability_catalog=catalog)
        with patch('nailong.core.tool_approval.get_config', return_value={'configurable': {'thread_id': 'chat'}}):
            _, plan = batch._prepare(message)
        self.assertEqual(plan['actions'], [])
        self.assertEqual(plan['rows'], {})

    def test_final_tools_disabled_request_is_not_stamped_as_callable(self):
        from langchain_core.messages import HumanMessage
        from nailong.core.capabilities import BOUND, CapabilityCatalog, CapabilityMiddleware
        from tools import build_tools
        catalog = CapabilityCatalog(explicit_tools={'read_file'})
        tools = build_tools(file_session=self.factory._tool_session('chat'), allowed_tools={'read_file'})
        catalog.register(tools)
        request = SimpleNamespace(tools=tools, state={'messages': [HumanMessage(content='读取项目')]})
        message = response()
        message.response_metadata['nailong_actual_bound_tools'] = []
        CapabilityMiddleware(catalog)._stamp(request, SimpleNamespace(result=[message]))
        self.assertEqual(message.response_metadata[BOUND]['names'], [])
