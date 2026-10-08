"""Child budget regressions: temporary holds, permanent refusals and accounting."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent import AgentRuntimeFactory
from config import Settings
from nailong.core.budgets import (
    SharedTokenBudget, TokenBudgetExceeded, active_token_budget,
)
from nailong.core.sessions import ProjectSessionStore
from nailong.tools.agents import ReadOnlyTaskRunner
from tests.test_safety_regressions import RecordingModel, paid_response


def request(text="hello"):
    return SimpleNamespace(messages=[HumanMessage(content=text)], system_message=None,
                           tools=[], model=SimpleNamespace(max_tokens=1000), model_settings={})


class ChildReservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_admission_diagnostic_does_not_claim_to_bound_full_model_input(self):
        rows = []
        runner = ReadOnlyTaskRunner(lambda _: asyncio.sleep(0, result="unreachable"),
            token_budget=0, record=lambda _thread, row: rows.append(row))
        result = await runner.run("t", "hello")
        self.assertIn("准入暂估", result)
        self.assertIsNone(rows[0]["input_tokens_upper_bound"])
        self.assertEqual(rows[0]["admission_tokens_estimate"], 1002)
        self.assertEqual(rows[0]["output_tokens_requested"], 1000)

    async def test_diagnostic_write_failure_cannot_leak_or_interrupt_budget_settlement(self):
        def record(_row):
            raise OSError("log unavailable")

        budget = SharedTokenBudget(10000, output_tokens=1000, record=record)
        hold, _ = await budget.reserve(request())
        await budget.settle(hold, [paid_response()])
        self.assertEqual(budget.reserved, 0)
        self.assertEqual(budget.remaining, 9890)

    async def test_second_child_waits_for_known_usage_to_release_temporary_hold(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        completed = []

        async def worker(prompt):
            budget = active_token_budget.get()
            hold, cap = await budget.reserve(request())
            self.assertLessEqual(budget.spent + budget.reserved, 2200)
            if prompt == "first":
                entered.set()
                await release.wait()
            await budget.settle(hold, [paid_response()])
            completed.append(prompt)
            return prompt

        runner = ReadOnlyTaskRunner(worker, token_budget=2200, child_output_tokens=1000)
        first = asyncio.create_task(runner.run("t", "first"))
        await entered.wait()
        second = asyncio.create_task(runner.run("t", "second"))
        await asyncio.sleep(0.02)
        self.assertFalse(second.done(), "temporary reservations must queue, not reject")
        release.set()
        self.assertEqual(await asyncio.wait_for(asyncio.gather(first, second), 1), ["first", "second"])
        self.assertEqual(completed, ["first", "second"])
        self.assertEqual(runner.remaining_budget("t"), 1980)

    async def test_cancelled_waiter_releases_admission_without_consuming_tokens(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def worker(prompt):
            budget = active_token_budget.get()
            hold, _ = await budget.reserve(request())
            entered.set()
            await release.wait()
            await budget.settle(hold, [paid_response()])
            return "done"

        runner = ReadOnlyTaskRunner(worker, token_budget=2200, child_output_tokens=1000)
        first = asyncio.create_task(runner.run("t", "first"))
        await entered.wait()
        waiter = asyncio.create_task(runner.run("t", "second"))
        await asyncio.sleep(0.02)
        self.assertFalse(waiter.done())
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        release.set()
        await first
        self.assertEqual(runner.remaining_budget("t"), 2090)

    async def test_impossible_task_is_not_restarted_until_next_parent_turn(self):
        calls = 0

        async def worker(prompt):
            nonlocal calls
            calls += 1
            await active_token_budget.get().reserve(request("x" * 4000))
            return "unreachable"

        runner = ReadOnlyTaskRunner(worker, token_budget=2200, child_output_tokens=100)
        result = await runner.run("t", "same task")
        again = await runner.run("t", "same task")
        self.assertEqual(calls, 1)
        self.assertIn("输入上界", result)
        self.assertIn("余额", result)
        self.assertIn("本轮", again)
        self.assertEqual(runner.remaining_budget("t"), 2200)
        self.assertIsNone(runner.drain_usage("t"))
        runner.begin_turn("t")
        await runner.run("t", "same task")
        self.assertEqual(calls, 2)

    async def test_unknown_provider_usage_stops_children_and_explains_why(self):
        budget = SharedTokenBudget(10000, output_tokens=1000)
        hold, _ = await budget.reserve(request())
        await budget.settle(hold, [AIMessage(content="unknown usage")])
        with self.assertRaises(TokenBudgetExceeded) as caught:
            await budget.reserve(request())
        self.assertIn("用量未知", str(caught.exception))
        self.assertEqual(caught.exception.diagnostics["reason"], "usage_unknown")
        self.assertEqual(budget.reserved, 0)
        self.assertEqual(budget.spent, hold)

    async def test_refusal_diagnostics_include_system_and_schema_input(self):
        budget = SharedTokenBudget(2000, output_tokens=1000)
        req = request()
        req.system_message = HumanMessage(content="s" * 2000)
        req.tools = [{"name": "probe", "description": "d" * 500}]
        with self.assertRaises(TokenBudgetExceeded) as caught:
            await budget.reserve(req)
        info = caught.exception.diagnostics
        self.assertGreater(info["input_tokens_upper_bound"], 3500)
        self.assertEqual(info["remaining_tokens"], 2000)
        self.assertEqual(info["output_tokens_requested"], 1000)
        self.assertEqual(info["spent_tokens"], 0)
        self.assertEqual(info["reserved_tokens"], 0)


class ChildRuntimeBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = ProjectSessionStore(self.root, base_dir=self.root / "data")
        self.factory = AgentRuntimeFactory(
            Settings("fixture-key", "https://api.invalid", "deepseek-flash", self.root),
            session_store=self.store,
        )

    async def asyncTearDown(self):
        await self.factory.aclose()
        self.factory.close()
        self.directory.cleanup()

    async def test_default_child_preserves_output_room_without_parent_sized_output(self):
        model = RecordingModel(responses=[paid_response("child answer")])
        self.factory.model = model
        answer = await self.factory.task_runner.run("t", "answer this question")
        self.assertEqual(answer, "child answer")
        self.assertGreater(model._calls[0]["max_tokens"], 64)
        self.assertLessEqual(model._calls[0]["max_tokens"], 1500)
        events = self.store.read_events("t")
        reservations = [r["data"] for r in events if r["kind"] == "subagent_budget"]
        self.assertTrue(any(r["event"] == "reserved" and r["input_tokens_upper_bound"] > 1000
                            and r["output_tokens_reserved"] <= 1500 for r in reservations))

    async def test_runtime_budget_refusal_does_not_repeat_child_build_or_provider_call(self):
        model = RecordingModel(responses=[paid_response("unreachable")])
        self.factory.model = model
        runner = ReadOnlyTaskRunner(self.factory._run_readonly_subagent, token_budget=5000)
        one = await runner.run("t", "research")
        two = await runner.run("t", "research")
        self.assertIn("预算", one)
        self.assertIn("本轮", two)
        self.assertEqual(len(model._calls), 0)
        self.assertEqual(runner.remaining_budget("t"), 5000)
        self.assertIsNone(runner.drain_usage("t"))

    async def test_two_children_can_both_answer_after_reading_with_real_smoke_usage(self):
        import json

        class ReadThenAnswer(RecordingModel):
            async def _agenerate(model, messages, stop=None, run_manager=None, **kwargs):
                model._calls.append({"messages": messages, **kwargs})
                evidence = [m for m in messages if m.type == "tool"]
                initial = next(m.content for m in messages if m.type == "human")
                path = "README.md" if "README.md" in initial else "calc.py"
                if evidence:
                    message = AIMessage(content=json.loads(evidence[-1].content)["content"],
                        usage_metadata={"input_tokens": 4122, "output_tokens": 88, "total_tokens": 4210})
                elif any("最终回答证据" in str(m.content) for m in messages):
                    message = AIMessage(content=str(messages[-1].content),
                        usage_metadata={"input_tokens": 4122, "output_tokens": 88, "total_tokens": 4210})
                else:
                    message = AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"path": path},
                        "id": path, "type": "tool_call"}], usage_metadata={"input_tokens": 3845,
                        "output_tokens": 126, "total_tokens": 3971})
                return ChatResult(generations=[ChatGeneration(message=message)])

        (self.root / "README.md").write_text("Tiny calculator entry: calc.py", newline='\n')
        (self.root / "calc.py").write_text("def add(a,b): return a+b", newline='\n')
        model = ReadThenAnswer(responses=[])
        self.factory.model = model
        replies = await asyncio.wait_for(asyncio.gather(
            self.factory.task_runner.run("parallel", "只读取 README.md，然后说明用途，不查其他文件。"),
            self.factory.task_runner.run("parallel", "只读取 calc.py，然后说明函数，不查其他文件。"),
        ), 3)
        self.assertIn("Tiny calculator", replies[0])
        self.assertIn("return a+b", replies[1])
        self.assertEqual(len(model._calls), 4)
        self.assertEqual(self.factory.task_runner.remaining_budget("parallel"), 13638)
        events = [r["data"] for r in self.store.read_events("parallel") if r["kind"] == "subagent_budget"]
        self.assertTrue(all(r["reserved_tokens"] + r["spent_tokens"] <= 30000 for r in events))
        self.assertEqual(events[-1]["reserved_tokens"], 0)

    async def test_large_read_keeps_a_bounded_tools_disabled_final_answer(self):
        (self.root / "probe.txt").write_text("visible evidence\n" + "x" * 20000, newline='\n')
        model = RecordingModel(responses=[
            paid_response("", tool_calls=[{"name": "read_file", "args": {"path": "probe.txt"}, "id": "r"}]),
            paid_response("部分读取：visible evidence，未核实其余内容。"),
        ])
        self.factory.model = model
        runner = ReadOnlyTaskRunner(self.factory._run_readonly_subagent, token_budget=20000)
        result = await runner.run("finish", "读取 probe.txt 并说明")
        self.assertEqual(len(model._calls), 2)
        self.assertIn("visible evidence", result)
        final = model._calls[-1]
        self.assertLessEqual(final["max_tokens"], 512)
        self.assertIn("最终回答证据", str(final["messages"][-1].content))
        self.assertIn("裁剪", str(final["messages"][-1].content))
        self.assertFalse(any(m.type == "tool" or getattr(m, "tool_calls", None) for m in final["messages"]))
        self.assertEqual(runner.remaining_budget("finish"), 19780)

    async def test_tools_returned_in_bounded_final_are_charged_and_never_executed(self):
        (self.root / 'probe.txt').write_text('x' * 20000, newline='\n')
        model = RecordingModel(responses=[
            paid_response('', tool_calls=[{'name': 'read_file', 'args': {'path': 'probe.txt'}, 'id': 'first'}]),
            paid_response('', tool_calls=[{'name': 'read_file', 'args': {'path': 'must-not-read.txt'}, 'id': 'late'}]),
        ])
        self.factory.model = model
        runner = ReadOnlyTaskRunner(self.factory._run_readonly_subagent, token_budget=20000)
        result = await runner.run('late-final', '读取 probe.txt 并说明')
        self.assertIn('最终回答仍请求工具', result)
        self.assertEqual(len(model._calls), 2)
        self.assertEqual(runner.usage_estimate('late-final')['total_tokens'], 220)
        self.assertEqual(runner.remaining_budget('late-final'), 19780)
        again = await runner.run('late-final', '读取 probe.txt 并说明')
        self.assertIn('未再次启动', again)
        self.assertEqual(len(model._calls), 2)


if __name__ == "__main__":
    unittest.main()
