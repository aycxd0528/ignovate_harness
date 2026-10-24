"""Exercise real graph and service behavior when reasoning consumes all output."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import PrivateAttr

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.sessions import ProjectSessionStore


def response(text='', reason='length', *, tools=None, invalid=None):
    return AIMessage(content=text, response_metadata={'finish_reason': reason},
        additional_kwargs={'reasoning_content': 'provider thinking tool context'},
        tool_calls=tools or [], invalid_tool_calls=invalid or [],
        usage_metadata={'input_tokens': 100, 'output_tokens': 4096 if reason == 'length' else 20,
                        'total_tokens': 4196 if reason == 'length' else 120,
                        'output_token_details': {'reasoning': 4096 if reason == 'length' else 0}})


class Model(FakeMessagesListChatModel):
    _requests: list = PrivateAttr(default_factory=list)

    def bind_tools(self, tools, **options):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **options):
        self._requests.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **options)


class GenerationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'project'
        self.root.mkdir()
        self.sessions = ProjectSessionStore(self.root, base_dir=Path(temporary.name)/'private')
        self.config = {'configurable': {'thread_id': 'one'}}

    async def execute(self, responses, **options):
        self.model = Model(responses=responses)
        with patch('agent.ChatDeepSeek', return_value=self.model):
            self.factory = AgentRuntimeFactory(Settings('fake-key', 'https://example.invalid', 'private', self.root,
                cli_preferences={'reasoning_effort': 'default'}), session_store=self.sessions)
        self.addCleanup(self.factory.close)
        self.addAsyncCleanup(self.factory.aclose)
        self.service = AgentService(self.factory)
        return [event async for event in self.service.stream_turn('请修复缓存功能，先只读分析当前项目并给出计划', self.config, **options)]

    async def test_reasoning_only_length_response_is_recovered_once_and_both_calls_are_logged(self):
        events = await self.execute([response(), response('完整计划', 'stop')])
        final = next(event.data for event in events if event.kind == 'final')
        self.assertEqual(final['text'], '完整计划')
        self.assertEqual(final['stats']['model_calls'], 2)
        self.assertEqual(len(self.model._requests), 2)
        self.assertNotEqual(self.model._requests[0], self.model._requests[1])
        self.assertTrue(any(getattr(m, 'additional_kwargs', {}).get('reasoning_content')
                            for m in self.model._requests[1]))
        records = [row['data'] for row in self.sessions.read_events('one') if row['kind'] == 'model_response']
        self.assertEqual([row['finish_reason'] for row in records], ['length', 'stop'])
        self.assertEqual(records[0]['output_reasoning_tokens'], 4096)
        self.assertEqual(records[0]['body_chars'], 0)
        self.assertEqual(len({row['model_call_id'] for row in records}), 2)
        usages = [event.data for event in events if event.kind == 'usage' and 'input_tokens' in event.data]
        self.assertEqual(sum(row['total_tokens'] for row in usages), 4316)
        self.assertEqual(final.get('output_status'), 'recovered')

    async def test_partial_answer_recovery_returns_a_complete_replacement(self):
        events = await self.execute([response('不完整步骤一'), response('完整步骤一、二、三', 'stop')])
        self.assertEqual(events[-1].data['text'], '完整步骤一、二、三')
        self.assertEqual(events[-1].data.get('output_status'), 'recovered')

    async def test_second_truncation_is_visible_unverified_and_no_third_request_runs(self):
        events = await self.execute([response(), response('未完成片段'), response('must not run', 'stop')])
        final = next(event.data for event in events if event.kind == 'final')
        self.assertEqual(final.get('output_status'), 'truncated')
        self.assertEqual(final['delivery']['status'], 'unverified')
        self.assertEqual(len(self.model._requests), 2)
        self.assertIn('未完成片段', final['text'])
        self.assertEqual(self.factory.task_store.snapshot('one')['lifecycle'], 'paused')

    async def test_truncated_tool_call_is_never_executed_even_if_arguments_parse(self):
        events = await self.execute([response(tools=[{'name': 'write_file', 'id': 'partial',
            'args': {'path': 'must-not-exist.txt', 'content': 'truncated body'}}]), response('must not run', 'stop')])
        self.assertFalse((self.root/'must-not-exist.txt').exists())
        self.assertFalse(any(event.kind == 'tool_start' for event in events))
        self.assertEqual(len(self.model._requests), 1)
        self.assertEqual(events[-1].data.get('output_status'), 'truncated')

    async def test_empty_stop_response_remains_distinct_from_length(self):
        events = await self.execute([response('', 'stop')])
        self.assertEqual(len(self.model._requests), 1)
        self.assertNotEqual(events[-1].data.get('output_status'), 'truncated')

    async def test_last_request_with_truncated_tool_has_one_usage_and_truncated_final(self):
        events = await self.execute([response(tools=[{'name': 'read_file', 'id': 'last',
            'args': {'path': 'README.md'}}])], max_model_calls=1)
        self.assertEqual(events[-1].data.get('output_status'), 'truncated')
        self.assertEqual(sum(event.data['total_tokens'] for event in events
            if event.kind == 'usage' and 'total_tokens' in event.data), 4196)
        self.assertFalse(any(event.kind == 'tool_start' for event in events))

    async def test_recovery_does_not_bypass_last_model_call_allowance(self):
        events = await self.execute([response(), response('must not run', 'stop')], max_model_calls=1)
        self.assertEqual(len(self.model._requests), 1)
        self.assertEqual(events[-1].data.get('output_status'), 'truncated')
        self.assertEqual(events[-1].data['delivery']['status'], 'unverified')

    async def test_empty_stop_after_truncation_cannot_be_recovered_delivery(self):
        events = await self.execute([response('部分答案'), response('', 'stop')])
        self.assertEqual(events[-1].data.get('output_status'), 'truncated')
        self.assertEqual(events[-1].data['delivery']['status'], 'unverified')

    async def test_whitespace_stop_after_truncation_cannot_be_recovered_delivery(self):
        events = await self.execute([response('部分答案'), response(' \n ', 'stop')])
        self.assertEqual(events[-1].data.get('output_status'), 'truncated')

    async def test_child_truncated_tool_call_never_executes(self):
        # A child has no write tools; use a read tool and verify it never emits
        # a ToolMessage, rather than relying only on least privilege to pass.
        await self.execute([response('main plan', 'stop')])
        self.model.responses = [response(tools=[{'name': 'read_file', 'id': 'partial-read',
            'args': {'path': 'README.md'}}])]
        result = await self.factory._run_readonly_subagent('阅读项目')
        self.assertIn('输出', result.text)
        self.assertEqual(result.budget_failure['reason'], 'output_truncated')
        self.assertEqual(len(self.model._requests), 2)
