"""Replay DeepSeek SSE through real SDK serialization without an API connection."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import openai

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.model import HarnessChatDeepSeek
from nailong.core.sessions import ProjectSessionStore


def sse_completion(*, identity, text, reasoning, finish_reason, inputs, outputs,
                   cache, reasoning_tokens):
    base = {'id': identity, 'object': 'chat.completion.chunk', 'created': 1,
            'model': 'deepseek-flash'}
    delta = {'role': 'assistant', 'content': text}
    if reasoning:
        delta['reasoning_content'] = reasoning
    chunks = [
        {**base, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
        {**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish_reason}]},
        {**base, 'choices': [], 'usage': {
            'prompt_tokens': inputs, 'completion_tokens': outputs,
            'total_tokens': inputs + outputs, 'prompt_cache_hit_tokens': cache,
            'prompt_cache_miss_tokens': inputs - cache,
            'completion_tokens_details': {'reasoning_tokens': reasoning_tokens},
        }},
    ]
    body = ''.join('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\n' for chunk in chunks)
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
        content=(body + 'data: [DONE]\n\n').encode('utf-8'))


class GenerationWireTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'project'
        self.root.mkdir()
        (self.root/'.nailong').mkdir()
        (self.root/'.nailong/settings.json').write_text(json.dumps({
            'budgets': {'session_token_limit': 300000}}))
        self.sessions = ProjectSessionStore(self.root, base_dir=Path(temporary.name)/'private')
        self.config = {'configurable': {'thread_id': 'wire'}}
        self.requests = []

    def factory(self, replay):
        def exchange(request):
            self.requests.append(request)
            return replay(request, len(self.requests))
        transport = httpx.MockTransport(exchange)
        sync = httpx.Client(transport=transport)
        asynchronous = httpx.AsyncClient(transport=transport)
        self.addCleanup(sync.close)
        self.addAsyncCleanup(asynchronous.aclose)

        def real_model(**kwargs):
            return HarnessChatDeepSeek(**kwargs, http_client=sync, http_async_client=asynchronous)

        with patch('agent.ChatDeepSeek', side_effect=real_model):
            factory = AgentRuntimeFactory(Settings('offline-fixture-key', 'https://deepseek.invalid/v1',
                'deepseek-flash', self.root,
                cli_preferences={'model': 'default', 'reasoning_effort': 'max'}),
                session_store=self.sessions)
        self.addCleanup(factory.close)
        self.addAsyncCleanup(factory.aclose)
        return factory

    async def test_reasoning_only_length_continuation_preserves_actual_wire_fields_and_single_usage(self):
        # Dropped thinking fields, lost reasoning_content, or duplicate stream usage
        # break the provider request and cumulative ledger at this boundary.
        def replay(_request, attempt):
            if attempt == 1:
                return sse_completion(identity='first', text='', reasoning='Inspect the existing project.',
                    finish_reason='length', inputs=100, outputs=131072, cache=80, reasoning_tokens=131072)
            if attempt == 2:
                return sse_completion(identity='second', text='完整计划', reasoning='Conclude from evidence.',
                    finish_reason='stop', inputs=200, outputs=50, cache=160, reasoning_tokens=30)
            raise AssertionError('only one continuation may be sent')

        factory = self.factory(replay)
        events = [event async for event in AgentService(factory).stream_turn(
            '请介绍当前项目并说明用途', self.config, allowed_tools=set())]
        final = events[-1].data
        self.assertEqual(final['text'], '完整计划')
        self.assertEqual(final['output_status'], 'recovered')
        self.assertEqual(final['stats']['model_calls'], 2)
        self.assertEqual(len(self.requests), 2)
        payloads = [json.loads(request.content) for request in self.requests]
        for payload in payloads:
            self.assertEqual(payload['max_tokens'], 131072)
            self.assertEqual(payload['reasoning_effort'], 'max')
            self.assertEqual(payload['thinking'], {'type': 'enabled'})
            self.assertTrue(payload['stream'])
        self.assertNotEqual(payloads[0]['messages'], payloads[1]['messages'])
        continuation = [row for row in payloads[1]['messages'] if row['role'] == 'assistant']
        self.assertEqual(len(continuation), 1)
        self.assertEqual(continuation[0]['reasoning_content'], 'Inspect the existing project.')
        usages = [event.data for event in events if event.kind == 'usage' and 'total_tokens' in event.data]
        self.assertEqual([row['total_tokens'] for row in usages], [131172, 250])
        self.assertEqual([row['cache_hit_tokens'] for row in usages], [80, 160])
        self.assertEqual(len({row['model_call_id'] for row in usages}), 2)
        self.assertEqual(factory.session_token_budget('wire').spent, 131422)
        records = [row['data'] for row in self.sessions.read_events('wire') if row['kind'] == 'model_response']
        self.assertEqual([row['finish_reason'] for row in records], ['length', 'stop'])
        self.assertEqual([row['output_reasoning_tokens'] for row in records], [131072, 30])
        self.assertEqual([row['body_chars'] for row in records], [0, 4])

    async def test_active_budget_disables_actual_sdk_retry_after_retryable_http_error(self):
        # Changing only the model attribute leaves SDK retries enabled unless its
        # existing root client is also cloned with max_retries=0.
        def replay(_request, _attempt):
            return httpx.Response(500, json={'error': {'message': 'offline retryable failure',
                'type': 'server_error', 'code': 'fixture'}})

        factory = self.factory(replay)
        self.assertEqual(factory.model.root_async_client.max_retries, 2)
        with self.assertRaises(openai.InternalServerError):
            [event async for event in AgentService(factory).stream_turn(
                '请介绍当前项目', self.config, allowed_tools=set())]
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].headers['x-stainless-retry-count'], '0')
        budget = factory.session_token_budget('wire')
        self.assertTrue(budget.stopped)
        self.assertEqual(budget.stop_reason, 'usage_unknown')
        self.assertGreaterEqual(budget.spent, 131072)
        self.assertEqual(budget.reserved, 0)


if __name__ == '__main__':
    unittest.main()
