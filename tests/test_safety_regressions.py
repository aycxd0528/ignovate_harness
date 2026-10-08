"""Regression cases for durable accounting and bounded execution."""

import asyncio
import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.message import add_messages
from pydantic import PrivateAttr

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from headless import run_print
from nailong.core.costs import CostEstimator
from nailong.core.goal import GoalStore
from nailong.core.permissions import PermissionEngine
from nailong.core.sessions import ProjectSessionStore
from nailong.core.usage import message_usage
from nailong.tools.agents import ReadOnlyTaskRunner
import nailong.core.budgets as budgets
from ui.presentation import SessionMetrics
from ui.flows import drive_goal


def paid_response(content="answer", *, tool_calls=None, input_tokens=100):
    return AIMessage(content=content, tool_calls=tool_calls or [], usage_metadata={
        "input_tokens": input_tokens, "output_tokens": 10, "total_tokens": input_tokens + 10,
    })


class RecordingModel(FakeMessagesListChatModel):
    _calls: list = PrivateAttr(default_factory=list)
    _block_after: int | None = PrivateAttr(default=None)
    _entered: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)
    _release: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)

    def bind_tools(self, tools, **kwargs):
        return self.bind(**kwargs)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls.append({"messages": messages, **kwargs})
        if self._block_after is not None and len(self._calls) > self._block_after:
            self._entered.set()
            await self._release.wait()
        return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)


class MessageGraph:
    def __init__(self, messages):
        self.messages = list(messages)

    async def aget_state(self, config):
        return SimpleNamespace(values={"messages": list(self.messages)})

    async def aupdate_state(self, config, update):
        self.messages = add_messages(self.messages, update["messages"])


class RewindSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_rewind_preserves_all_102_prior_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ProjectSessionStore(root, base_dir=root / "data")
            prior = [HumanMessage(id=f"prior-{i}", content=str(i)) for i in range(102)]
            graph = MessageGraph(prior + [HumanMessage(id="new-user", content="new"), AIMessage(id="new-ai", content="answer")])
            store.append_event("long", "turn_start", {"message_ids": [m.id for m in prior]})

            async def runtime(**kwargs):
                return graph

            service = AgentService(SimpleNamespace(async_runtime=runtime), session_store=store, permission_engine=PermissionEngine(root))
            removed = await service.rewind("long")

        self.assertEqual(removed, 2)
        self.assertEqual([m.id for m in graph.messages], [f"prior-{i}" for i in range(102)])

    async def test_legacy_100_id_boundary_is_rejected_without_deleting_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ProjectSessionStore(root, base_dir=root / "data")
            store.ensure_directories()
            ids = [f"prior-{i}" for i in range(102)]
            graph = MessageGraph([HumanMessage(id=value, content=value) for value in ids])
            store.session_path("legacy").write_text(json.dumps({"kind": "turn_start", "data": {"message_ids": ids[:100]}}) + "\n", newline='\n')

            async def runtime(**kwargs):
                return graph

            service = AgentService(SimpleNamespace(async_runtime=runtime), session_store=store, permission_engine=PermissionEngine(root))
            with self.assertRaisesRegex(ValueError, "边界"):
                await service.rewind("legacy")
            self.assertEqual([m.id for m in graph.messages], ids)

    def test_repeated_rewind_keeps_paid_usage_once(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProjectSessionStore(directory, base_dir=Path(directory) / "data")
            store.append_event("thread", "turn_start", {"message_ids": []})
            store.append_event("thread", "usage", {"input_tokens": 10, "output_tokens": 1})
            store.append_event("thread", "final", {"text": "first"})
            store.append_event("thread", "turn_start", {"message_ids": ["u1", "a1"]})
            store.append_event("thread", "usage", {"input_tokens": 20, "output_tokens": 2})
            store.append_event("thread", "usage_missing", {"scope": "main"})
            store.append_event("thread", "final", {"text": "second"})
            store.truncate_events("thread", store.last_turn_boundary("thread")[0] - 1)
            store.truncate_events("thread", store.last_turn_boundary("thread")[0] - 1)
            events = store.read_events("thread")

        self.assertEqual([e["kind"] for e in events], ["usage", "usage", "usage_missing"])
        self.assertEqual([e["data"]["input_tokens"] for e in events if e["kind"] == "usage"], [10, 20])
        self.assertTrue(all(e["data"].get("rewound") for e in events))

    def test_rewound_usage_counts_toward_totals_but_not_current_context(self):
        metrics = SessionMetrics.from_events([
            {"kind": "turn_start", "data": {}},
            {"kind": "usage", "data": {"input_tokens": 100, "output_tokens": 10}},
            {"kind": "usage", "data": {"input_tokens": 200, "output_tokens": 20, "rewound": True}},
        ])
        self.assertEqual(metrics.turns, 1)
        self.assertEqual(metrics.input_tokens, 300)
        self.assertEqual(metrics.output_tokens, 30)
        self.assertEqual(metrics.last_input_tokens, 100)


class GoalStateSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = GoalStore(Path(self.temporary.name) / "goals.json")

    def test_paid_round_is_settled_after_model_pauses_goal(self):
        goal = self.store.create("work", thread_id="thread")
        self.store.update(goal.id, state="paused", summary="等待确认", thread_id="thread")
        result = self.store.record_round(goal.id, cost_usd=0.01, files_changed=False, tool_calls=1)
        self.assertEqual(result.round, 1)
        self.assertAlmostEqual(result.spent_usd, 0.01)
        self.assertEqual(result.state, "paused")
        self.assertEqual(result.pause_reason, "等待确认")

    def test_round_settlement_is_idempotent(self):
        goal = self.store.create("work")
        for _ in range(2):
            self.store.record_round(goal.id, cost_usd=0.01, files_changed=True, tool_calls=1, round_id="one-round")
        saved = self.store.get(goal.id)
        self.assertEqual(saved.round, 1)
        self.assertAlmostEqual(saved.spent_usd, 0.01)

    def test_resume_rejects_exhausted_rounds(self):
        goal = self.store.create("work", max_rounds=1)
        self.store.record_round(goal.id, cost_usd=0.01, files_changed=True, tool_calls=1)
        ok, reason, result = self.store.resume(goal.id)
        self.assertFalse(ok)
        self.assertEqual(result.state, "paused")
        self.assertIn("轮数", reason)

    def test_resume_rejects_exhausted_cost(self):
        goal = self.store.create("work", max_cost_usd=0.01)
        self.store.record_round(goal.id, cost_usd=0.01, files_changed=True, tool_calls=1)
        ok, reason, result = self.store.resume(goal.id)
        self.assertFalse(ok)
        self.assertEqual(result.state, "paused")
        self.assertIn("成本", reason)

    def test_resume_requires_fresh_verification_even_on_same_thread(self):
        goal = self.store.create("work", thread_id="thread")
        self.store.record_verification("check", {"ok": True, "exit_code": 0}, thread_id="thread")
        self.store.update(goal.id, state="paused", thread_id="thread")
        self.store.resume(goal.id, thread_id="thread")
        ok, reason, _ = self.store.update(goal.id, state="complete", thread_id="thread")
        self.assertFalse(ok)
        self.assertIn("验证", reason)

    def test_rebinding_active_goal_invalidates_old_verification(self):
        goal = self.store.create("work", thread_id="A")
        self.store.record_verification("check", {"ok": True, "exit_code": 0}, thread_id="A")
        self.store.attach_thread(goal.id, "B")
        ok, _, _ = self.store.update(goal.id, state="complete", thread_id="B")
        self.assertFalse(ok)

    def test_prepare_round_stops_legacy_active_goal_at_round_limit(self):
        goal = self.store.create("work", max_rounds=1)
        payload = json.loads(self.store.path.read_text())
        payload[0]["round"] = 1
        self.store.path.write_text(json.dumps(payload), newline='\n')
        result = self.store.prepare_round(goal.id)
        self.assertEqual(result.state, "paused")
        self.assertEqual(result.round, 1)
        self.assertIn("轮数", result.pause_reason)


class RuntimeTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        (self.root / "probe.txt").write_text("test data", newline='\n')
        self.settings = Settings("local-test-key", "https://api.deepseek.com", "deepseek-flash", self.root)
        self.factory = AgentRuntimeFactory(self.settings, session_store=ProjectSessionStore(self.root, base_dir=Path(self.temporary.name) / "data"))
        self.service = AgentService(self.factory, api_key=self.settings.api_key)
        self.estimator = CostEstimator(self.settings.model, self.root)

    async def asyncTearDown(self):
        await self.factory.aclose()
        self.factory.close()
        self.temporary.cleanup()


class GoalExecutionSafetyTests(RuntimeTestCase):
    async def test_cancel_during_settled_goal_status_pauses_without_double_charging(self):
        self.factory.model = RecordingModel(responses=[
            paid_response("", tool_calls=[{"name": "read_file", "args": {"path": "probe.txt"}, "id": "read"}]),
            paid_response("已检查"),
        ])
        goal = self.factory.goal_store.create("work", thread_id="status-cancel")

        def emit(event):
            if event.kind == "goal_status":
                raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await drive_goal(self.service, self.factory, self.factory.goal_store, self.estimator, goal, thread_id="status-cancel", emit=emit)
        saved = self.factory.goal_store.get(goal.id)
        self.assertEqual(saved.state, "paused")
        self.assertEqual(saved.round, 1)
        self.assertAlmostEqual(saved.spent_usd, 0.000084)
        self.assertEqual(len(saved.settled_round_ids), 1)
        self.assertIn("取消", saved.pause_reason)

    async def test_model_pausing_goal_still_settles_the_entire_round(self):
        self.factory.model = RecordingModel(responses=[
            paid_response("", tool_calls=[{"name": "update_goal", "args": {"state": "paused", "summary": "需要用户确认"}, "id": "pause"}]),
            paid_response("已暂停"),
        ])
        goal = self.factory.goal_store.create("work", thread_id="model-pauses")
        result = await drive_goal(self.service, self.factory, self.factory.goal_store, self.estimator, goal, thread_id="model-pauses", emit=lambda _: None)
        self.assertEqual(result.state, "paused")
        self.assertEqual(result.round, 1)
        self.assertAlmostEqual(result.spent_usd, 0.000084)
        self.assertIn("需要用户确认", result.pause_reason)

    async def test_parent_cancellation_persists_child_usage_before_round_settlement(self):
        model = RecordingModel(responses=[
            paid_response("", tool_calls=[{"name": "task", "args": {"description": "读取 probe.txt"}, "id": "child"}]),
            paid_response("", tool_calls=[{"name": "read_file", "args": {"path": "probe.txt"}, "id": "read"}]),
            paid_response("not reached"),
        ])
        model._block_after = 2
        self.factory.model = model
        goal = self.factory.goal_store.create("work", thread_id="parent-cancel")
        task = asyncio.create_task(drive_goal(self.service, self.factory, self.factory.goal_store, self.estimator, goal, thread_id="parent-cancel", emit=lambda _: None))
        await asyncio.wait_for(model._entered.wait(), timeout=3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        child_events = [event for event in self.factory.session_store.read_events("parent-cancel") if event["kind"] == "usage" and event["data"].get("scope") == "subagent"]
        self.assertEqual(len(child_events), 1)
        self.assertEqual(child_events[0]["data"]["total_tokens"], 110)
        self.assertTrue(child_events[0]["data"]["estimated"])
        saved = self.factory.goal_store.get(goal.id)
        self.assertEqual(saved.state, "paused")
        self.assertEqual(saved.round, 1)
        self.assertGreater(saved.spent_usd, 0.000084)

    async def test_cancel_after_paid_usage_settles_and_pauses_goal(self):
        self.factory.model = RecordingModel(responses=[paid_response()])
        goal = self.factory.goal_store.create("work", thread_id="cancel")

        def emit(event):
            if event.kind == "usage":
                raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await drive_goal(self.service, self.factory, self.factory.goal_store, self.estimator, goal, thread_id="cancel", emit=emit)
        saved = self.factory.goal_store.get(goal.id)
        self.assertEqual(saved.state, "paused")
        self.assertEqual(saved.round, 1)
        self.assertAlmostEqual(saved.spent_usd, 0.000042)

    def blocking_model(self):
        model = RecordingModel(responses=[
            paid_response("", tool_calls=[{"name": "read_file", "args": {"path": "probe.txt"}, "id": "read"}]),
            paid_response("not reached"),
        ])
        model._block_after = 1
        self.factory.model = model
        return model

    async def test_cancel_during_model_call_keeps_prior_charge_and_reservation(self):
        model = self.blocking_model()
        goal = self.factory.goal_store.create("work", thread_id="cancel-model")
        task = asyncio.create_task(drive_goal(self.service, self.factory, self.factory.goal_store, self.estimator, goal, thread_id="cancel-model", emit=lambda _: None))
        await asyncio.wait_for(model._entered.wait(), timeout=3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        saved = self.factory.goal_store.get(goal.id)
        self.assertEqual(saved.state, "paused")
        self.assertEqual(saved.round, 1)
        self.assertGreater(saved.spent_usd, 0.000042)

    async def test_headless_cancel_settles_and_pauses_goal(self):
        model = self.blocking_model()
        goal = self.factory.goal_store.create("work", thread_id="headless-cancel")
        with patch("headless.create_agent_runtime", side_effect=lambda _: self.factory(thread_id="headless-cancel")):
            task = asyncio.create_task(run_print(self.settings, "work", goal_mode=True, stdout=StringIO(), stderr=StringIO()))
            await asyncio.wait_for(model._entered.wait(), timeout=3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        saved = self.factory.goal_store.get(goal.id)
        self.assertEqual(saved.state, "paused")
        self.assertEqual(saved.round, 1)
        self.assertGreater(saved.spent_usd, 0.000042)

    async def test_stale_active_goal_cannot_start_another_round(self):
        model = RecordingModel(responses=[paid_response()])
        self.factory.model = model
        goal = self.factory.goal_store.create("work", max_rounds=1, thread_id="stale")
        self.factory.goal_store.record_round(goal.id, cost_usd=0, files_changed=True, tool_calls=1)
        result = await drive_goal(self.service, self.factory, self.factory.goal_store, self.estimator, goal, thread_id="stale", emit=lambda _: None)
        self.assertEqual(result.round, 1)
        self.assertEqual(len(model._calls), 0)


class SubagentBudgetSafetyTests(RuntimeTestCase):
    async def test_child_uses_bounded_final_instead_of_oversized_next_research_request(self):
        (self.root / "probe.txt").write_text("x" * 20_000, newline='\n')
        model = RecordingModel(responses=[
            paid_response("", tool_calls=[{"name": "read_file", "args": {"path": "probe.txt"}, "id": "read"}]),
            paid_response("只取得部分文件内容，其余内容未核实。"),
        ])
        self.factory.model = model
        # Funding another full research request would exceed the remaining
        # allowance; a bounded tools-disabled final request uses its own hold.
        runner = ReadOnlyTaskRunner(self.factory._run_readonly_subagent, token_budget=23_000)
        result = await runner.run("limited", "读取文件")
        self.assertEqual(len(model._calls), 2)
        self.assertIn('未核实', result)
        final = model._calls[-1]
        # With no registered tools LangChain sends an unbound model request;
        # providers may omit tool_choice rather than serialize "none".
        self.assertIn(final.get('tool_choice'), {None, 'none'})
        self.assertLessEqual(final.get('max_tokens'), 512)
        self.assertFalse(any(message.type == 'tool' or getattr(message, 'tool_calls', None)
            for message in final['messages']))
        self.assertIn('最终回答证据', str(final['messages'][-1].content))
        self.assertTrue(any('工具已禁用' in str(message.content) for message in final['messages']))
        request = SimpleNamespace(messages=final['messages'], system_message=None, tools=[])
        self.assertLessEqual(budgets._input_bound(request) + final['max_tokens'], 23000 - 110)
        self.assertEqual(runner.usage_estimate("limited")["total_tokens"], 220)
        self.assertEqual(runner.remaining_budget('limited'), 23000 - 220)

    async def test_child_output_cap_is_applied_to_model_request(self):
        model = RecordingModel(responses=[paid_response()])
        self.factory.model = model
        runner = ReadOnlyTaskRunner(self.factory._run_readonly_subagent, child_output_tokens=16)
        await runner.run("limited", "回答")
        self.assertEqual(model._calls[0].get("max_tokens"), 16)

    async def test_child_cancel_keeps_usage_from_successful_calls(self):
        model = RecordingModel(responses=[
            paid_response("", tool_calls=[{"name": "read_file", "args": {"path": "probe.txt"}, "id": "read"}]),
            paid_response("not reached"),
        ])
        model._block_after = 1
        self.factory.model = model
        runner = self.factory.task_runner
        task = asyncio.create_task(runner.run("cancel-child", "读取文件"))
        await asyncio.wait_for(model._entered.wait(), timeout=3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        usage = runner.drain_usage("cancel-child")
        self.assertIsNotNone(usage)
        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["output_tokens"], 10)
        self.assertTrue(usage["estimated"])
        self.assertIsNone(runner.drain_usage("cancel-child"))

    async def test_missing_usage_prevents_further_child_model_calls(self):
        model = RecordingModel(responses=[AIMessage(content="no usage")])
        self.factory.model = model
        runner = self.factory.task_runner
        await runner.run("missing", "回答")
        second = await runner.run("missing", "再次回答")
        self.assertEqual(len(model._calls), 1)
        self.assertIn("预算", second)
        self.assertTrue(runner.drain_usage("missing")["estimated"])
        self.assertLess(runner.remaining_budget("missing"), 29_000)

    async def test_concurrent_reservations_share_one_token_budget(self):
        budget = budgets.SharedTokenBudget(2_000, output_tokens=1_000)
        request = SimpleNamespace(messages=[HumanMessage(content="hello")], system_message=None, tools=[], model=SimpleNamespace(max_tokens=1_000), model_settings={})
        reservation, cap = await budget.reserve(request)
        self.assertGreater(cap, 0)
        self.assertLessEqual(cap, 1_000)
        with self.assertRaises(budgets.TokenBudgetExceeded):
            await budget.reserve(request)
        await budget.settle(reservation, [paid_response()])
        self.assertEqual(budget.remaining, 1_890)


class UsageMetadataSafetyTests(unittest.TestCase):
    def test_missing_or_invalid_token_counts_are_not_known_zero_usage(self):
        for value in ({}, {"prompt_tokens": 10}, {"input_tokens": -1, "output_tokens": 2}, {"input_tokens": "bad", "output_tokens": 2}):
            with self.subTest(value=value):
                message = SimpleNamespace(usage_metadata=None, response_metadata={"token_usage": value})
                self.assertIsNone(message_usage(message))


if __name__ == "__main__":
    unittest.main()
