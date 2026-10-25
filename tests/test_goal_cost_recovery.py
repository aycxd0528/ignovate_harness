"""Durable dollars survive provider/usage crash windows without replay."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from nailong.core.budgets import CostBudgetExceeded, GoalCostBudget
from nailong.core.costs import CostEstimator
from nailong.core.goal import GoalStore


def request():
    return SimpleNamespace(messages=[HumanMessage(content='inspect source')], system_message=None,
        tools=[], model_settings={'max_tokens':128}, model=None)


def response():
    return AIMessage(content='observed', usage_metadata={
        'input_tokens':100, 'output_tokens':10, 'total_tokens':110})


class GoalCostRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=GoalStore(self.root/'goals.json')
        self.goal=self.store.create('complete work',thread_id='owner',max_cost_usd=.1)
        self.estimator=CostEstimator('deepseek-flash',self.root)
        self.cost=self.estimator.estimate({'input_tokens':100,'output_tokens':10})

    def budget(self,round_id='native-round'):
        budget=GoalCostBudget(self.estimator,.1,goal_store=self.store,
            goal_id=self.goal.id,round_id=round_id)
        self.addCleanup(budget.close)
        return budget

    async def test_reservation_is_durable_before_the_caller_can_start_provider(self):
        budget=self.budget();hold,_=await budget.reserve(request())
        saved=GoalStore(self.store.path).get(self.goal.id)
        row=saved.pending_cost_rounds['native-round']
        self.assertEqual(list(row['reservations'].values()),[float(hold)])
        self.assertEqual(saved.spent_usd,0)
        await budget.release(hold)
        self.assertEqual(self.store.get(self.goal.id).pending_cost_rounds,{})

    async def test_known_usage_is_immediate_and_round_closeout_does_not_rebill(self):
        budget=self.budget();hold,_=await budget.reserve(request())
        await budget.settle(hold,[response()])
        self.assertAlmostEqual(GoalStore(self.store.path).get(self.goal.id).spent_usd,self.cost)
        saved=self.store.record_round(self.goal.id,cost_usd=budget.spent,
            files_changed=True,tool_calls=1,round_id=budget.goal_round_id)
        self.assertAlmostEqual(saved.spent_usd,self.cost)
        self.assertEqual(saved.round,1);self.assertEqual(saved.pending_cost_rounds,{})
        self.store.record_round(self.goal.id,cost_usd=budget.spent,files_changed=True,
            tool_calls=1,round_id=budget.goal_round_id)
        self.assertAlmostEqual(self.store.get(self.goal.id).spent_usd,self.cost)

    async def test_identical_concurrent_amounts_have_distinct_reservation_identity(self):
        budget=self.budget();first,_=await budget.reserve(request());second,_=await budget.reserve(request())
        self.assertEqual(float(first),float(second))
        await asyncio.gather(budget.settle(second,[response()]),budget.release(first))
        self.assertAlmostEqual(budget.spent,self.cost);self.assertEqual(budget.reserved,0)
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):await budget.settle(second,[response()])
        self.assertEqual(self.store.path.read_bytes(),before)

    async def test_recovery_skips_a_live_round_lease(self):
        budget=self.budget();hold,_=await budget.reserve(request())
        fresh=GoalStore(self.store.path)
        self.assertEqual(fresh.costs.recover(),[])
        saved=fresh.get(self.goal.id)
        self.assertEqual(saved.state,'active');self.assertEqual(saved.spent_usd,0)
        self.assertIn('native-round',saved.pending_cost_rounds)
        await budget.release(hold)

    async def test_another_process_cannot_recover_a_live_round(self):
        budget=self.budget();hold,_=await budget.reserve(request())
        script="""import json,sys
from nailong.core.goal import GoalStore
store=GoalStore(sys.argv[1]);changed=store.costs.recover();goal=store.latest()
print(json.dumps({'changed':changed,'state':goal.state,'spent':goal.spent_usd,'pending':len(goal.pending_cost_rounds)}))
"""
        result=subprocess.run([sys.executable,'-c',script,str(self.store.path)],
            capture_output=True,timeout=15,cwd=Path(__file__).resolve().parent.parent)
        self.assertEqual(result.returncode,0,result.stderr.decode())
        self.assertEqual(json.loads(result.stdout),{'changed':[],'state':'active','spent':0,'pending':1})
        await budget.release(hold)

    async def test_orphaned_unknown_reservation_is_charged_once_and_paused(self):
        budget=self.budget();hold,_=await budget.reserve(request());budget.close()
        fresh=GoalStore(self.store.path)
        self.assertEqual(fresh.costs.recover(),[self.goal.id])
        saved=fresh.get(self.goal.id)
        self.assertEqual(saved.state,'paused');self.assertEqual(saved.round,1)
        self.assertAlmostEqual(saved.spent_usd,float(hold))
        self.assertEqual(saved.settled_round_ids,['native-round']);self.assertEqual(saved.pending_cost_rounds,{})
        self.assertIn('未知',saved.pause_reason)
        self.assertEqual(fresh.costs.recover(),[])
        self.assertEqual(fresh.get(self.goal.id).spent_usd,saved.spent_usd)

    async def test_orphaned_known_usage_is_not_added_twice(self):
        budget=self.budget();hold,_=await budget.reserve(request())
        await budget.settle(hold,[response()]);budget.close()
        self.store.costs.recover();saved=self.store.get(self.goal.id)
        self.assertAlmostEqual(saved.spent_usd,self.cost)
        self.assertEqual(saved.round,1);self.assertEqual(saved.state,'paused')

    async def test_reusing_an_orphaned_round_identity_does_not_restart_provider(self):
        first=self.budget();hold,_=await first.reserve(request());first.close()
        second=self.budget()
        with self.assertRaises(CostBudgetExceeded):await second.reserve(request())
        saved=self.store.get(self.goal.id)
        self.assertAlmostEqual(saved.spent_usd,float(hold))
        self.assertEqual(saved.round,1);self.assertEqual(saved.state,'paused')

    async def test_reservation_write_failure_refuses_admission_without_replacing_ledger(self):
        budget=self.budget();before=self.store.path.read_bytes()
        with patch.object(self.store,'_save',side_effect=OSError('disk full')):
            with self.assertRaises(CostBudgetExceeded):await budget.reserve(request())
        self.assertEqual(self.store.path.read_bytes(),before)
        self.assertEqual(budget.reserved,0)

    async def test_usage_write_failure_retains_unknown_hold_for_recovery(self):
        budget=self.budget();hold,_=await budget.reserve(request())
        before=self.store.path.read_bytes()
        with patch.object(self.store,'_save',side_effect=OSError('disk full')):
            with self.assertRaises(CostBudgetExceeded):await budget.settle(hold,[response()])
        self.assertEqual(self.store.path.read_bytes(),before)
        budget.close();self.store.costs.recover()
        self.assertAlmostEqual(self.store.get(self.goal.id).spent_usd,float(hold))

    async def test_missing_usage_consumes_reservation_and_cannot_complete_round(self):
        budget=self.budget();hold,_=await budget.reserve(request())
        await budget.settle(hold,[])
        self.assertFalse(budget.usage_complete)
        saved=self.store.record_round(self.goal.id,cost_usd=budget.spent,
            files_changed=True,tool_calls=1,round_id=budget.goal_round_id)
        self.assertAlmostEqual(saved.spent_usd,float(hold));self.assertEqual(saved.state,'paused')

    async def test_actual_usage_over_reservation_is_kept_and_blocks_next_call(self):
        budget=self.budget();hold,_=await budget.reserve(request())
        message=AIMessage(content='large response',usage_metadata={
            'input_tokens':1000000,'output_tokens':10000,'total_tokens':1010000})
        expected=self.estimator.estimate({'input_tokens':1000000,'output_tokens':10000})
        self.assertGreater(expected,float(hold))
        await budget.settle(hold,[message])
        self.assertAlmostEqual(self.store.get(self.goal.id).spent_usd,expected)
        with self.assertRaises(CostBudgetExceeded):await budget.reserve(request())
        saved=self.store.record_round(self.goal.id,cost_usd=budget.spent,
            files_changed=True,tool_calls=1,round_id=budget.goal_round_id)
        self.assertAlmostEqual(saved.spent_usd,expected);self.assertEqual(saved.state,'paused')

    async def test_live_round_can_pay_for_final_response_after_native_pause(self):
        budget=self.budget();first,_=await budget.reserve(request());await budget.settle(first,[response()])
        self.store.update(self.goal.id,state='paused',summary='model awaits user',thread_id='owner')
        final,_=await budget.reserve(request());await budget.settle(final,[response()])
        saved=self.store.record_round(self.goal.id,cost_usd=budget.spent,
            files_changed=False,tool_calls=1,round_id=budget.goal_round_id)
        self.assertEqual(saved.state,'paused');self.assertAlmostEqual(saved.spent_usd,2*self.cost)

    async def test_explicit_resume_first_reconciles_an_unknown_round_without_running_it(self):
        budget=self.budget();hold,_=await budget.reserve(request());budget.close()
        self.store.update(self.goal.id,state='paused',summary='user paused',thread_id='owner')
        ok,reason,saved=self.store.resume(self.goal.id,thread_id='owner')
        self.assertFalse(ok);self.assertIn('未知',reason)
        self.assertAlmostEqual(saved.spent_usd,float(hold));self.assertEqual(saved.state,'paused')
        ok,reason,saved=self.store.resume(self.goal.id,thread_id='owner')
        self.assertTrue(ok,reason);self.assertEqual(saved.state,'active')

    async def test_recovery_marker_cannot_be_bypassed_by_a_paused_planning_budget(self):
        budget=self.budget();hold,_=await budget.reserve(request());budget.close();self.store.costs.recover()
        planning=GoalCostBudget(self.estimator,.1,goal_store=self.store,
            goal_id=self.goal.id,round_id='planning',allow_paused=True)
        self.addCleanup(planning.close)
        with self.assertRaises(CostBudgetExceeded):await planning.reserve(request())
        self.assertAlmostEqual(self.store.get(self.goal.id).spent_usd,float(hold))

    async def test_unknown_or_inflight_provider_cost_blocks_goal_completion(self):
        budget=self.budget();hold,_=await budget.reserve(request())
        ok,reason,_=self.store.update(self.goal.id,state='complete',thread_id='owner')
        self.assertFalse(ok);self.assertIn('费用',reason)
        await budget.settle(hold,[])
        ok,reason,_=self.store.update(self.goal.id,state='complete',thread_id='owner')
        self.assertFalse(ok);self.assertIn('费用',reason)

    async def test_recovery_marker_requires_paused_state(self):
        rows=json.loads(self.store.path.read_text());rows[0]['cost_recovery_required']=True
        raw=json.dumps(rows).encode();self.store.path.write_bytes(raw)
        with self.assertRaises(ValueError):self.store.active()
        self.assertEqual(self.store.path.read_bytes(),raw)

    async def test_malformed_pending_costs_fail_closed_and_legacy_records_still_load(self):
        budget=self.budget();hold,_=await budget.reserve(request());await budget.release(hold)
        valid=json.loads(self.store.path.read_text())
        invalid=[[],{'r':{}},{'r':{'charged_usd':float('nan'),'usage_complete':True,'reservations':{}}},
            {'r':{'charged_usd':0,'usage_complete':True,'reservations':{'hold':-1}}},
            {'r':{'charged_usd':1,'usage_complete':True,'reservations':{}}},
            {'r':{'charged_usd':0,'usage_complete':True,'reservations':{'hold':10**1000}}}]
        for pending in invalid:
            with self.subTest(pending=pending):
                rows=json.loads(json.dumps(valid));rows[0]['pending_cost_rounds']=pending
                raw=json.dumps(rows).encode();self.store.path.write_bytes(raw)
                with self.assertRaises(ValueError):GoalStore(self.store.path).active()
                self.assertEqual(self.store.path.read_bytes(),raw)
        valid[0].pop('pending_cost_rounds');self.store.path.write_text(json.dumps(valid))
        self.assertEqual(GoalStore(self.store.path).get(self.goal.id).pending_cost_rounds,{})


class GoalCostHardExitTests(unittest.TestCase):
    def test_real_hard_exit_does_not_free_unknown_or_known_costs_on_restart(self):
        script='''import asyncio,os,sys
from pathlib import Path
from nailong.core.goal import GoalStore
from nailong.core.budgets import GoalCostBudget
from nailong.core.costs import CostEstimator
from test_goal_cost_recovery import request,response
root=Path(sys.argv[1]);store=GoalStore(root/'goals.json');store.costs.recover()
goal=store.latest()
if goal.state!='active':os._exit(92)
async def run():
    budget=GoalCostBudget(CostEstimator('deepseek-flash',root),.1,goal_store=store,goal_id=goal.id,round_id='crashed')
    hold,_=await budget.reserve(request())
    with (root/'provider.calls').open('a') as stream:stream.write('called\\n')
    if sys.argv[2]=='known':await budget.settle(hold,[response()])
    os._exit(91)
asyncio.run(run())
'''
        environment={**os.environ,'PYTHONPATH':os.pathsep.join([str(Path(__file__).resolve().parent.parent),
            str(Path(__file__).resolve().parent)])}
        for window in ('unknown','known'):
            with self.subTest(window=window),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);store=GoalStore(root/'goals.json')
                goal=store.create('complete work',thread_id='owner',max_cost_usd=.1)
                result=subprocess.run([sys.executable,'-c',script,str(root),window],env=environment,
                    capture_output=True,timeout=20)
                self.assertEqual(result.returncode,91,result.stderr.decode())
                restarted=subprocess.run([sys.executable,'-c',script,str(root),window],env=environment,
                    capture_output=True,timeout=20)
                self.assertEqual(restarted.returncode,92,restarted.stderr.decode())
                self.assertEqual((root/'provider.calls').read_text().splitlines(),['called'])
                saved=store.get(goal.id)
                self.assertEqual(saved.state,'paused');self.assertGreater(saved.spent_usd,0)
                self.assertEqual(saved.round,1);self.assertEqual(saved.pending_cost_rounds,{})
