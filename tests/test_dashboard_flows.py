import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_service import TurnEvent
from nailong.core.costs import CostEstimator
from nailong.core.goal import GoalStore
from nailong.core.plan import PlanStore
from ui.flows import drive_goal, run_plan_flow


class DashboardFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_flow_approves_draft_then_executes_in_chat_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(directory)

            class Service:
                def __init__(self):
                    self.calls = []

                async def stream_turn(self, message, config, **kwargs):
                    self.calls.append((message, kwargs))
                    if kwargs["profile"] == "plan":
                        yield TurnEvent("plan_ready", {"plan_id": store.stage("# 原计划")})
                    yield TurnEvent("final", {"text": "完成"})

            service = Service()
            emitted = []
            executed = await run_plan_flow(
                service, SimpleNamespace(plan_store=store),
                thread_id="plan-thread",
                config={"configurable": {"thread_id": "plan-thread"}},
                prompt_message="制定计划",
                emit=emitted.append,
                confirm=lambda draft: "approve",
                edit=lambda draft: draft,
            )
            saved = list((Path(directory) / ".nailong" / "plans").glob("*.md"))

        self.assertTrue(executed)
        self.assertEqual([call[1]["profile"] for call in service.calls], ["plan", "chat"])
        self.assertTrue(service.calls[1][1]["pin_message"])
        self.assertEqual(len(saved), 1)
        self.assertIn("plan_ready", [event.kind for event in emitted])

    async def test_plan_flow_rejection_discards_draft_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(directory)
            calls = []

            class Service:
                async def stream_turn(self, message, config, **kwargs):
                    calls.append(kwargs["profile"])
                    yield TurnEvent("plan_ready", {"plan_id": store.stage("# 不执行")})

            executed = await run_plan_flow(
                Service(), SimpleNamespace(plan_store=store),
                thread_id="plan-reject", config={}, prompt_message="制定计划",
                emit=lambda event: None, confirm=lambda draft: "reject", edit=lambda draft: draft,
            )
            plan_dir = Path(directory) / ".nailong" / "plans"
            self.assertFalse(plan_dir.exists())

        self.assertFalse(executed)
        self.assertEqual(calls, ["plan"])

    async def test_plan_flow_edit_requires_second_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(directory)
            decisions = iter(["edit", "approve"])

            class Service:
                async def stream_turn(self, message, config, **kwargs):
                    if kwargs["profile"] == "plan":
                        yield TurnEvent("plan_ready", {"plan_id": store.stage("# 原计划")})
                    else:
                        yield TurnEvent("final", {"text": "执行完成"})

            executed = await run_plan_flow(
                Service(), SimpleNamespace(plan_store=store),
                thread_id="plan-edit", config={}, prompt_message="制定计划",
                emit=lambda event: None, confirm=lambda draft: next(decisions),
                edit=lambda draft: "# 用户编辑的计划",
            )
            saved = list((Path(directory) / ".nailong" / "plans").glob("*.md"))
            self.assertEqual(saved[0].read_text(encoding="utf-8"), "# 用户编辑的计划\n")

        self.assertTrue(executed)

    async def test_goal_flow_records_round_and_pauses_at_round_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory) / "state" / "goals.json")
            goal = store.create("检查文件", max_rounds=1, max_cost_usd=1.0, thread_id="goal-thread")
            calls = []

            class Service:
                async def stream_turn(self, message, config, **kwargs):
                    calls.append(kwargs)
                    yield TurnEvent("usage", {"input_tokens": 100, "output_tokens": 10, "cache_hit_tokens": 0})
                    yield TurnEvent("tool_start", {"name": "read_file"})
                    yield TurnEvent("final", {"text": "已检查"})

            result = await drive_goal(
                Service(), SimpleNamespace(task_runner=None), store,
                CostEstimator("deepseek-flash", directory), goal,
                thread_id="goal-thread", emit=lambda event: None,
            )

        self.assertEqual(result.state, "paused")
        self.assertEqual(result.round, 1)
        self.assertIn("轮数", result.pause_reason)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["pin_message"], "goal")

    async def test_goal_flow_without_approval_handler_pauses_unattended(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory) / "state" / "goals.json")
            goal = store.create("修改文件", max_rounds=4, max_cost_usd=1.0, thread_id="goal-thread")

            class Service:
                async def stream_turn(self, message, config, **kwargs):
                    yield TurnEvent("approval_needed", {"actions": [{"name": "write_file"}]})
                    yield TurnEvent("final", {"text": "等待审批"})

            result = await drive_goal(
                Service(), SimpleNamespace(task_runner=None), store,
                CostEstimator("deepseek-flash", directory), goal,
                thread_id="goal-thread", emit=lambda event: None, approval=None,
            )

        self.assertEqual(result.state, "paused")
        self.assertIn("审批", result.pause_reason)
