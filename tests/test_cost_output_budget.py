"""Cost admission preserves generation settings or refuses the whole call."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from nailong.core.budgets import CostBudgetExceeded, GoalCostBudget
from nailong.core.costs import CostEstimator


class CostOutputBudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / '.nailong').mkdir()
        (self.root / '.nailong/settings.json').write_text(json.dumps({'pricing': {'fixture': {
            'input_per_million': 0, 'cache_hit_per_million': 0, 'output_per_million': 1}}}))
        self.estimator = CostEstimator('fixture', self.root)

    def request(self, output=131072):
        return SimpleNamespace(model=SimpleNamespace(max_tokens=output), model_settings={},
                               messages=[], system_message=None, tools=[])

    async def test_full_configured_reasoning_output_is_reserved(self):
        budget = GoalCostBudget(self.estimator, .5)
        hold, cap = await budget.reserve(self.request())
        self.assertEqual(cap, 131072)
        self.assertAlmostEqual(hold, .131072)
        self.assertAlmostEqual(budget.reserved, .131072)

    async def test_admission_preview_reports_full_cost_without_reserving_or_stopping(self):
        budget=GoalCostBudget(self.estimator,.1)
        self.assertTrue(callable(getattr(budget,'preview',None)))
        info=budget.preview(self.request())
        self.assertFalse(info['can_reserve'])
        self.assertAlmostEqual(info['required_usd'],.131072)
        self.assertAlmostEqual(info['remaining_usd'],.1)
        self.assertEqual(info['output_tokens'],131072)
        self.assertEqual(budget.reserved,0)
        self.assertFalse(budget.stopped)

    async def test_refusal_contains_atomic_cost_diagnostics(self):
        budget=GoalCostBudget(self.estimator,.1)
        with self.assertRaises(CostBudgetExceeded) as caught:
            await budget.reserve(self.request())
        info=getattr(caught.exception,'diagnostics',{})
        self.assertAlmostEqual(info.get('required_usd',0),.131072)
        self.assertAlmostEqual(info.get('remaining_usd',0),.1)
        self.assertEqual(info.get('output_tokens'),131072)

    async def test_scoped_preview_respects_either_scope_without_spending(self):
        from nailong.core.budgets import ScopedCostBudget
        for local_limit,parent_limit in ((.5,.1),(.1,.5)):
            local=GoalCostBudget(self.estimator,local_limit)
            parent=GoalCostBudget(self.estimator,parent_limit)
            info=ScopedCostBudget(local,parent).preview(self.request())
            self.assertFalse(info['can_reserve'])
            self.assertAlmostEqual(info['remaining_usd'],.1)
            self.assertFalse(local.stopped or parent.stopped)
            self.assertEqual(local.reserved+parent.reserved,0)

    async def test_insufficient_funds_refuse_instead_of_reducing_output(self):
        budget = GoalCostBudget(self.estimator, .1)
        with self.assertRaises(CostBudgetExceeded):
            await budget.reserve(self.request())
        self.assertEqual(budget.reserved, 0)
        self.assertEqual(budget.spent, 0)
        self.assertTrue(budget.stopped)

    async def test_exact_funding_and_explicit_small_output_remain_usable(self):
        budget = GoalCostBudget(self.estimator, .002048)
        hold, cap = await budget.reserve(self.request(2048))
        self.assertEqual(cap, 2048)
        self.assertAlmostEqual(hold, .002048)
        await budget.release(hold)
        self.assertEqual(budget.reserved, 0)

    async def test_concurrent_admissions_do_not_spend_the_same_output_funds(self):
        budget = GoalCostBudget(self.estimator, .2)
        outcomes = await asyncio.gather(budget.reserve(self.request()),
                                       budget.reserve(self.request()), return_exceptions=True)
        self.assertEqual(sum(isinstance(x, CostBudgetExceeded) for x in outcomes), 1)
        self.assertLessEqual(budget.reserved, .2)

    async def test_known_usage_releases_unused_output_reservation(self):
        budget = GoalCostBudget(self.estimator, .5)
        hold, _ = await budget.reserve(self.request())
        await budget.settle(hold, [AIMessage(content='done', usage_metadata={
            'input_tokens': 10, 'output_tokens': 100, 'total_tokens': 110})])
        self.assertEqual(budget.reserved, 0)
        self.assertAlmostEqual(budget.spent, .0001)
        self.assertFalse(budget.stopped)

    async def test_unknown_usage_keeps_the_full_reservation_charged_and_stops(self):
        budget = GoalCostBudget(self.estimator, .5)
        hold, _ = await budget.reserve(self.request())
        await budget.settle(hold, [])
        self.assertAlmostEqual(budget.spent, .131072)
        self.assertEqual(budget.reserved, 0)
        self.assertFalse(budget.usage_complete)
        self.assertTrue(budget.stopped)

    async def test_scoped_rejected_round_releases_parent_hold(self):
        from nailong.core.budgets import ScopedCostBudget
        parent, local = GoalCostBudget(self.estimator,.5), GoalCostBudget(self.estimator,.01)
        with self.assertRaises(CostBudgetExceeded):
            await ScopedCostBudget(local,parent).reserve(self.request())
        self.assertEqual(parent.reserved,0)
        self.assertEqual(parent.spent,0)

    async def test_scoped_unknown_usage_charges_and_stops_both_budgets(self):
        from nailong.core.budgets import ScopedCostBudget
        parent, local = GoalCostBudget(self.estimator,.5), GoalCostBudget(self.estimator,.5)
        scoped=ScopedCostBudget(local,parent)
        hold,_=await scoped.reserve(self.request())
        await scoped.settle(hold,[])
        for budget in (parent,local):
            self.assertAlmostEqual(budget.spent,.131072)
            self.assertEqual(budget.reserved,0)
            self.assertTrue(budget.stopped)

    async def test_scoped_release_does_not_charge_either_budget(self):
        from nailong.core.budgets import ScopedCostBudget
        parent, local = GoalCostBudget(self.estimator,.5), GoalCostBudget(self.estimator,.5)
        scoped=ScopedCostBudget(local,parent)
        hold,_=await scoped.reserve(self.request())
        await scoped.release(hold)
        for budget in (parent,local):
            self.assertEqual(budget.spent,0)
            self.assertEqual(budget.reserved,0)


class ExplicitAuthorizationTests(unittest.TestCase):
    def test_real_implementation_authorization_is_work(self):
        from nailong.core.task_requests import task_request_kind
        for text in ('现在授权按你的方案实现，并增加必要的回归测试。',
                     '我同意你按计划开始实施。', '批准按方案执行。',
                     '授权你继续修改代码。'):
            with self.subTest(text=text):
                self.assertEqual(task_request_kind(text), 'work')

    def test_questions_negative_and_quoted_authorizations_do_not_start_work(self):
        from nailong.core.task_requests import task_request_kind
        for text in ('现在授权按方案实现是否安全？', '暂不授权按方案实现，先解释一下。',
                     '同意按方案执行吗？', '授权你实现吗？',
                     '现在授权按方案实现，是否安全？',
                     '现在授权按方案实现这件事，我不同意。',
                     '现在授权按方案实现，但暂不实施。',
                     '以下是原文：\n"\n现在授权按你的方案实现。\n"',
                     "以下是原文：\n'\n现在授权按你的方案实现。\n'",
                     '历史记录：\n现在授权按你的方案实现。',
                     '现在授权按方案实现，但不要执行任务。',
                     '历史记录：\n> 现在授权按你的方案实现。',
                     '以下是日志：\n```text\n现在授权按你的方案实现。\n```',
                     '请解释“现在授权按你的方案实现”的含义。'):
            with self.subTest(text=text):
                self.assertNotIn(task_request_kind(text), {'work', 'resume'})
