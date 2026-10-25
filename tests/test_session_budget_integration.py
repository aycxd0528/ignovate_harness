"""Cumulative admission must apply at the actual main and recovery boundary."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage

from agent import AgentRuntimeFactory, ModelAccountingMiddleware
from agent_service import AgentService
from config import Settings
from nailong.core.cumulative_budget import SessionTokenBudget, active_session_token_budget
from nailong.core.sessions import ProjectSessionStore
from test_generation_recovery import Model, response


class SessionBudgetIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'project'
        self.root.mkdir()
        (self.root/'.nailong').mkdir()
        (self.root/'.nailong/settings.json').write_text(json.dumps({'budgets': {'session_token_limit': 20000}}))
        self.sessions = ProjectSessionStore(self.root, base_dir=Path(temporary.name)/'private')
        self.config = {'configurable': {'thread_id': 'one'}}

    def factory(self, responses, **options):
        self.model = Model(responses=responses)
        with patch('agent.ChatDeepSeek', return_value=self.model):
            factory = AgentRuntimeFactory(Settings('fake-key', 'https://example.invalid', 'private', self.root,
                cli_preferences={'reasoning_effort': 'default'}), session_store=self.sessions, **options)
        self.addCleanup(factory.close)
        self.addAsyncCleanup(factory.aclose)
        return factory

    async def test_operator_can_set_limit_without_writing_benchmark_workspace(self):
        (self.root/'.nailong/settings.json').unlink()
        factory = self.factory([response('unused', 'stop')], session_token_limit=120000)
        budget = factory.session_token_budget('one')
        self.assertEqual(budget.limit, 120000)
        self.assertFalse((self.root/'.nailong/settings.json').exists())
        self.assertTrue(budget.ledger_path.is_relative_to(self.sessions.root))

    async def test_cumulative_budget_stops_before_main_provider_call(self):
        (self.root/'.nailong/settings.json').write_text(json.dumps({'budgets': {'session_token_limit': 3992}}))
        factory = self.factory([response('must not run', 'stop')])
        events = [event async for event in AgentService(factory).stream_turn('请分析当前项目', self.config)]
        self.assertEqual(len(self.model._requests), 0)
        self.assertEqual(events[-1].data.get('output_status'), 'budget_stopped')
        self.assertEqual(events[-1].data['delivery']['status'], 'unverified')

    async def test_recovery_uses_same_cumulative_budget_and_emits_first_usage_on_refusal(self):
        # The graph's full system/tool request exceeds 20k bytes; use a larger
        # ceiling but charge most of it in the first synthetic provider response.
        (self.root/'.nailong/settings.json').write_text(json.dumps({'budgets': {'session_token_limit': 300000}}))
        first = response()
        first.usage_metadata = {'input_tokens': 270000, 'output_tokens': 4096, 'total_tokens': 274096}
        factory = self.factory([first, response('must not run', 'stop')])
        events = [event async for event in AgentService(factory).stream_turn('请分析当前项目', self.config)]
        self.assertEqual(len(self.model._requests), 1)
        self.assertEqual(events[-1].data.get('output_status'), 'budget_stopped')
        self.assertEqual(sum(event.data['total_tokens'] for event in events
                             if event.kind == 'usage' and 'total_tokens' in event.data), 274096)

    async def test_admission_failure_does_not_spend_an_unsent_turn_request(self):
        from nailong.core.budgets import TurnModelBudget, active_turn_budget, TurnModelLimitExceeded
        budget = SessionTokenBudget(20000)
        self.addCleanup(budget.close)
        request = ModelRequest(model=Model(responses=[response('unused', 'stop')]), tools=[],
            messages=[HumanMessage(content='hello')], model_settings={'max_tokens': 1000})
        turn = TurnModelBudget(1)
        turn.admit()
        session_token = active_session_token_budget.set(budget)
        turn_token = active_turn_budget.set(turn)
        try:
            with self.assertRaises(TurnModelLimitExceeded):
                await ModelAccountingMiddleware(main_agent=True).awrap_model_call(request,
                    lambda prepared: self.fail('provider must not be called'))
        finally:
            active_session_token_budget.reset(session_token)
            active_turn_budget.reset(turn_token)
        self.assertEqual(budget.spent, 0)
        self.assertEqual(budget.reserved, 0)
        self.assertFalse(budget.stopped)

    async def test_cancelled_provider_is_charged_and_stops_session(self):
        budget = SessionTokenBudget(20000)
        self.addCleanup(budget.close)
        request = ModelRequest(model=Model(responses=[response('unused', 'stop')]), tools=[],
            messages=[HumanMessage(content='hello')], model_settings={'max_tokens': 1000})
        token = active_session_token_budget.set(budget)
        async def cancelled(prepared):
            raise asyncio.CancelledError()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await ModelAccountingMiddleware().awrap_model_call(request, cancelled)
        finally:
            active_session_token_budget.reset(token)
        self.assertGreater(budget.spent, 1000)
        self.assertEqual(budget.reserved, 0)
        self.assertTrue(budget.stopped)

    async def test_factory_reuses_cumulative_budget_across_turns_and_dialog_rewind(self):
        # Recreating per-turn budgets or refunding dialog rewind would erase spend.
        (self.root/'.nailong/settings.json').write_text(json.dumps({
            'budgets': {'session_token_limit': 300000}}))
        factory = self.factory([response('第一轮介绍', 'stop'), response('第二轮介绍', 'stop')])
        service = AgentService(factory)
        first = [event async for event in service.stream_turn('请介绍当前项目', self.config)]
        budget = factory.session_token_budget('one')
        self.assertEqual(first[-1].data['text'], '第一轮介绍')
        self.assertEqual(budget.spent, 120)
        second = [event async for event in service.stream_turn('再说明项目用途', self.config)]
        self.assertEqual(second[-1].data['text'], '第二轮介绍')
        self.assertIs(factory.session_token_budget('one'), budget)
        self.assertEqual(budget.spent, 240)
        self.assertEqual(budget.reserved, 0)
        await service.rewind('one')
        self.assertEqual(budget.spent, 240)
        self.assertEqual(json.loads(budget.ledger_path.read_text())['spent_tokens'], 240)

    async def test_closed_factory_restores_spend_after_project_budget_configuration_is_removed(self):
        # Removing project config must not turn an established limited session unlimited.
        (self.root/'.nailong/settings.json').write_text(json.dumps({
            'budgets': {'session_token_limit': 300000}}))
        factory = self.factory([response('原进程回答', 'stop')])
        first = [event async for event in AgentService(factory).stream_turn('请介绍当前项目', self.config)]
        self.assertEqual(first[-1].data['text'], '原进程回答')
        ledger_path = factory.session_token_budget('one').ledger_path
        await factory.aclose()
        (self.root/'.nailong/settings.json').unlink()
        restored = self.factory([response('重启后回答', 'stop')])
        budget = restored.session_token_budget('one')
        self.assertEqual(budget.limit, 300000)
        self.assertEqual(budget.spent, 120)
        self.assertEqual(budget.remaining, 299880)
        second = [event async for event in AgentService(restored).stream_turn('继续介绍项目用途', self.config)]
        self.assertEqual(second[-1].data['text'], '重启后回答')
        self.assertEqual(budget.spent, 240)
        self.assertEqual(json.loads(ledger_path.read_text())['spent_tokens'], 240)

    async def test_existing_empty_object_ledger_cannot_disable_budget_after_configuration_removal(self):
        # Treating {} as an absent ledger silently drops the established limit.
        factory = self.factory([response('must not run', 'stop')])
        path = factory.session_token_budget('one').ledger_path
        await factory.aclose()
        path.write_text('{}')
        (self.root/'.nailong/settings.json').unlink()
        restored = self.factory([response('must not run', 'stop')])
        with self.assertRaises(ValueError):
            restored.session_token_budget('one')
        self.assertEqual(path.read_text(), '{}')
        self.assertEqual(self.model._requests, [])

    async def test_invalid_budget_configuration_preserves_all_caller_context_variables(self):
        # Setting turn contexts before config validation strands the caller's values.
        from agent import active_review_coverage
        from nailong.core.budgets import active_turn_budget
        from nailong.core.generation import active_model_responses
        factory = self.factory([response('must not run', 'stop')])
        service = AgentService(factory)
        variables = (active_turn_budget, service._turn_task, active_review_coverage,
            active_session_token_budget, active_model_responses, service._hook_approval)
        expected = [object() for _ in variables]
        tokens = [variable.set(value) for variable, value in zip(variables, expected)]
        try:
            for invalid in (True, 0, '120000'):
                with self.subTest(limit=invalid):
                    (self.root/'.nailong/settings.json').write_text(json.dumps({
                        'budgets': {'session_token_limit': invalid}}))
                    with self.assertRaises(ValueError):
                        [event async for event in service.stream_turn('请介绍当前项目', self.config)]
                    for variable, value in zip(variables, expected):
                        self.assertIs(variable.get(), value, variable.name)
            self.assertEqual(self.model._requests, [])
        finally:
            for variable, token in reversed(list(zip(variables, tokens))):
                variable.reset(token)
