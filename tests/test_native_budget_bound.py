"""Native reservations count wire input, excluding internal response telemetry."""
import unittest

from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage

from nailong.core.budgets import _input_bound
from nailong.core.cumulative_budget import SessionTokenBudget
from nailong.core.model import HarnessChatDeepSeek


class NativeBudgetBoundTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_response_metadata_does_not_refuse_a_small_native_call(self):
        model = HarnessChatDeepSeek(model='deepseek-flash', api_key='fake', base_url='https://example.invalid',
            max_tokens=1000, max_retries=0)
        message = AIMessage(content='Previous answer', response_metadata={'internal_trace': 'x'*50000},
            additional_kwargs={'reasoning_content': 'required thinking'})
        request = ModelRequest(model=model, messages=[message, HumanMessage(content='Continue')], tools=[])
        wire = model._get_request_payload(request.messages)
        self.assertNotIn('internal_trace', wire['messages'][0])
        self.assertIn('reasoning_content', wire['messages'][0])
        budget = SessionTokenBudget(10000)
        self.addCleanup(budget.close)
        hold, cap = await budget.reserve(request)
        self.assertEqual(cap, 1000)
        self.assertLess(hold.amount, 10000)
        await budget.release(hold)

    async def test_required_reasoning_still_counts_against_native_admission(self):
        model = HarnessChatDeepSeek(model='deepseek-flash', api_key='fake', base_url='https://example.invalid',
            max_tokens=1000, max_retries=0)
        short = ModelRequest(model=model, tools=[], messages=[AIMessage(content='Answer',
            additional_kwargs={'reasoning_content': 'thought'})])
        long = short.override(messages=[short.messages[0].model_copy(update={
            'additional_kwargs': {'reasoning_content': 'thought'*1000}})])
        self.assertGreaterEqual(_input_bound(long)-_input_bound(short), 5994)
