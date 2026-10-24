"""Real child model calls share the parent's durable dollar round."""
import unittest
from unittest.mock import patch

import test_adaptive_task_flow as fixtures


class NativeChildCostTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.GoalStreamTests.asyncSetUp
    asyncTearDown=fixtures.GoalStreamTests.asyncTearDown

    async def test_parallel_children_and_parent_survive_closeout_failure_without_rebilling(self):
        self.factory.task_store.begin('stream',self.goal.objective)
        self.factory.model.responses=[fixtures.answer('',[
            {'name':'discover_tools','args':{'query':'task','limit':10},'id':'discover'}]),fixtures.answer('',[
            {'name':'task','args':{'description':'调查 probe.txt 的用途'},'id':'child-one'},
            {'name':'task','args':{'description':'调查 probe.txt 的已知限制'},'id':'child-two'}]),
            fixtures.answer('第一份调查，尚未读取文件。'),fixtures.answer('第二份调查，尚未读取文件。'),
            fixtures.answer('汇总两份未验证调查。')]
        events=[]
        with patch.object(self.goals,'record_round',side_effect=OSError('round closeout unavailable')):
            with self.assertRaises(OSError):
                async for event in fixtures.GoalStreamTests.stream(self):events.append(event)
        native_calls=[event for event in self.factory.session_store.read_events('stream')
            if event['kind'] in {'usage','model_usage_call'}]
        self.assertTrue(any(event['kind']=='model_usage_call' for event in native_calls),native_calls)
        expected=5*self.estimator.estimate({'input_tokens':100,'output_tokens':10})
        saved=self.goals.get(self.goal.id)
        self.assertAlmostEqual(saved.spent_usd,expected)
        self.assertEqual(saved.round,0)
        self.assertEqual(len(saved.pending_cost_rounds),1)
        self.assertEqual(self.goals.costs.recover(),[self.goal.id])
        recovered=self.goals.get(self.goal.id)
        self.assertAlmostEqual(recovered.spent_usd,expected)
        self.assertEqual(recovered.round,1);self.assertEqual(recovered.state,'paused')
        self.assertEqual(recovered.pending_cost_rounds,{})

    async def test_foreign_active_goal_cannot_hide_this_threads_recovery_barrier(self):
        from nailong.core.budgets import GoalCostBudget
        from test_goal_cost_recovery import request,response
        self.factory.task_store.begin('stream',self.goal.objective)
        budget=GoalCostBudget(self.estimator,1,goal_store=self.goals,
            goal_id=self.goal.id,round_id='crashed-owner-round')
        try:
            hold,_=await budget.reserve(request());await budget.settle(hold,[response()])
        finally:budget.close()
        self.goals.costs.recover()
        self.goals.create('独立任务',thread_id='foreign')
        before=self.goals.get(self.goal.id).spent_usd
        events=[event async for event in self.service.stream_turn('请继续修复原会话的项目',
            {'configurable':{'thread_id':'stream'}})]
        self.assertFalse(any(event.kind in {'usage','tool_start'} for event in events))
        self.assertTrue(any(event.kind=='notice' and '核对' in event.data.get('text','') for event in events))
        self.assertEqual(self.goals.get(self.goal.id).spent_usd,before)

    async def test_explicit_resume_keeps_original_allowance_and_recovers_no_cost_twice(self):
        from nailong.core.budgets import GoalCostBudget
        from test_goal_cost_recovery import request,response
        self.factory.task_store.begin('stream',self.goal.objective)
        budget=GoalCostBudget(self.estimator,1,goal_store=self.goals,
            goal_id=self.goal.id,round_id='crashed-owner-round')
        try:
            hold,_=await budget.reserve(request());await budget.settle(hold,[response()])
        finally:budget.close()
        self.goals.costs.recover()
        before=self.goals.get(self.goal.id)
        events=[event async for event in self.service.stream_turn('继续',{'configurable':{'thread_id':'stream'}})]
        self.assertTrue(any(event.kind=='usage' for event in events))
        saved=self.goals.get(self.goal.id)
        self.assertEqual(saved.id,before.id);self.assertEqual(saved.max_cost_usd,before.max_cost_usd)
        self.assertEqual(saved.round,2);self.assertFalse(saved.cost_recovery_required)
        self.assertAlmostEqual(saved.spent_usd,2*before.spent_usd)

    async def test_ambiguous_recovery_ledgers_do_not_select_a_budget_by_recency(self):
        from nailong.core.budgets import GoalCostBudget
        from test_goal_cost_recovery import request,response
        for identity in ('first','second'):
            goal=self.goal if identity=='first' else self.goals.create('other old objective',thread_id='stream')
            budget=GoalCostBudget(self.estimator,1,goal_store=self.goals,goal_id=goal.id,round_id=identity)
            try:
                hold,_=await budget.reserve(request());await budget.settle(hold,[response()])
            finally:budget.close()
            self.goals.costs.recover()
        self.factory.task_store.begin('stream','task without a proven ledger',new=True)
        self.goals.create('foreign active',thread_id='foreign')
        events=[event async for event in self.service.stream_turn('请修改项目',{'configurable':{'thread_id':'stream'}})]
        self.assertFalse(any(event.kind in {'usage','tool_start'} for event in events))
        self.assertTrue(any(event.kind=='notice' and '多个' in event.data.get('text','') for event in events))
