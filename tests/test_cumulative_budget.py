"""Cumulative session limits fund complete requests and survive process restarts."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from langchain.agents.middleware import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from nailong.core.budgets import TokenBudgetExceeded, active_token_budget
from nailong.core import cumulative_budget as budget_module


class BudgetModel(FakeMessagesListChatModel):
    max_tokens: int | None = 32768


def request(text="hello", *, output=32768, settings=None, system=None, tools=None):
    return ModelRequest(model=BudgetModel(responses=[], max_tokens=output),
        messages=[HumanMessage(content=text)], tools=tools or [], system_message=system,
        model_settings=settings or {})


def response(inputs=100, output=40):
    return AIMessage(content="answer", usage_metadata={
        "input_tokens": inputs, "output_tokens": output, "total_tokens": inputs + output,
        "input_token_details": {"cache_read": min(inputs, 80)},
        "output_token_details": {"reasoning": min(output, 30)},
    })


class SessionBudgetFixture(unittest.IsolatedAsyncioTestCase):
    def budget(self, tokens=120000, **kwargs):
        budget = budget_module.SessionTokenBudget(tokens, **kwargs)
        self.addCleanup(budget.close)
        return budget


class SessionBudgetTests(SessionBudgetFixture):
    async def test_3992_remaining_refuses_the_trial_30077_input(self):
        # A reset to the full ceiling or a partial output cap would permit this call.
        budget = self.budget()
        hold, _ = await budget.reserve(request("x" * 85000))
        await budget.settle(hold, [response(85000, 31008)])
        self.assertEqual(budget.remaining, 3992)
        with self.assertRaises(TokenBudgetExceeded) as caught:
            await budget.reserve(request("x" * 28916))
        self.assertEqual(caught.exception.diagnostics["input_tokens_upper_bound"], 30077)
        self.assertEqual(caught.exception.diagnostics["remaining_tokens"], 3992)
        self.assertEqual(caught.exception.diagnostics["scope"], "session")
        self.assertNotIn("子代理", str(caught.exception))

    async def test_complete_max_reasoning_output_is_reserved(self):
        budget = self.budget(40000)
        hold, cap = await budget.reserve(request())
        self.assertEqual(cap, 32768)
        self.assertEqual(budget.reserved, 33934)  # 1166 input upper bound + 32768 output.
        self.assertEqual(budget.remaining, 6066)
        await budget.release(hold)
        self.assertEqual(budget.remaining, 40000)

    async def test_insufficient_output_room_refuses_instead_of_reducing_to_64(self):
        budget = self.budget(1230)
        with self.assertRaises(TokenBudgetExceeded) as caught:
            await budget.reserve(request(output=1000))
        self.assertEqual(caught.exception.diagnostics["output_tokens_requested"], 1000)
        self.assertEqual(budget.reserved, 0)
        self.assertEqual(budget.spent, 0)

    async def test_context_prepared_output_setting_is_reserved_in_full(self):
        budget = self.budget(4000)
        hold, cap = await budget.reserve(request(settings={"max_tokens": 2048}))
        self.assertEqual(cap, 2048)
        self.assertEqual(budget.reserved, 3214)
        await budget.release(hold)

    async def test_optional_output_allowance_does_not_silently_cut_configured_output(self):
        budget = self.budget(output_tokens=16000)
        with self.assertRaises(TokenBudgetExceeded) as caught:
            await budget.reserve(request())
        self.assertEqual(caught.exception.diagnostics["reason"], "output_allowance_exceeded")
        self.assertEqual(budget.reserved, 0)

    async def test_output_allowance_supplies_unconfigured_model_default(self):
        budget = self.budget(4000, output_tokens=2000)
        hold, cap = await budget.reserve(request(output=None))
        self.assertEqual(cap, 2000)
        await budget.release(hold)

    async def test_invalid_limit_and_output_allowance_are_rejected_without_coercion(self):
        for value in (True, False, 0, -1, 1.5, "120000", None):
            with self.subTest(limit=value), self.assertRaises(ValueError):
                self.budget(value)
        for value in (True, False, 0, -1, 1.5, "4096"):
            with self.subTest(output=value), self.assertRaises(ValueError):
                self.budget(output_tokens=value)

    async def test_invalid_configured_output_is_rejected_without_default_or_coercion(self):
        budget = self.budget()
        for value in (True, False, 0, -1, 1.5, "4096", None):
            with self.subTest(output=value), self.assertRaises(ValueError):
                await budget.reserve(request(settings={"max_tokens": value}))
        self.assertEqual(budget.reserved, 0)

    async def test_concurrent_calls_cannot_share_the_same_complete_output_capacity(self):
        budget = self.budget(4000)
        results = await asyncio.gather(budget.reserve(request(output=2000)),
            budget.reserve(request(output=2000)), return_exceptions=True)
        admitted = [row for row in results if isinstance(row, tuple)]
        refused = [row for row in results if isinstance(row, TokenBudgetExceeded)]
        self.assertEqual(len(admitted), 1)
        self.assertEqual(len(refused), 1)
        self.assertEqual(admitted[0][1], 2000)
        self.assertEqual(budget.reserved, 3166)
        await budget.settle(admitted[0][0], [response()])
        second, cap = await budget.reserve(request(output=2000))
        self.assertEqual(cap, 2000)
        self.assertEqual(budget.spent, 140)
        await budget.release(second)

    async def test_cache_and_reasoning_details_are_included_once(self):
        budget = self.budget(4000)
        hold, _ = await budget.reserve(request(output=2000))
        await budget.settle(hold, [response()])
        self.assertEqual(budget.spent, 140)
        self.assertEqual(budget.remaining, 3860)
        self.assertEqual(budget.reserved, 0)

    async def test_cumulative_usage_continues_across_calls_and_child_contexts(self):
        budget = self.budget(4000)
        child = object()
        child_token = active_token_budget.set(child)
        session_token = budget_module.active_session_token_budget.set(budget)
        try:
            async def call():
                current = budget_module.active_session_token_budget.get()
                self.assertIs(active_token_budget.get(), child)
                hold, _ = await current.reserve(request(output=1000))
                await current.settle(hold, [response()])
            await asyncio.gather(call(), call())
            await call()
            self.assertEqual(budget.spent, 420)
            self.assertEqual(budget.remaining, 3580)
        finally:
            active_token_budget.reset(child_token)
            budget_module.active_session_token_budget.reset(session_token)

    async def test_unknown_provider_usage_charges_hold_and_stops_session(self):
        budget = self.budget(10000)
        hold, _ = await budget.reserve(request(output=2000))
        await budget.settle(hold, [AIMessage(content="missing usage")])
        self.assertEqual(budget.spent, 3166)
        self.assertEqual(budget.reserved, 0)
        with self.assertRaises(TokenBudgetExceeded) as caught:
            await budget.reserve(request(output=2000))
        self.assertIsInstance(caught.exception, budget_module.SessionTokenBudgetExceeded)
        self.assertEqual(caught.exception.diagnostics["reason"], "usage_unknown")
        self.assertEqual(caught.exception.diagnostics["scope"], "session")

    async def test_partial_usage_cannot_undercharge_known_responses(self):
        budget = self.budget(10000)
        hold, _ = await budget.reserve(request(output=2000))
        await budget.settle(hold, [response(5000, 100), AIMessage(content="unknown")])
        self.assertEqual(budget.spent, 5100)
        self.assertEqual(budget.stop_reason, "usage_unknown")

    async def test_provider_usage_above_hold_stops_further_calls(self):
        budget = self.budget(10000)
        hold, _ = await budget.reserve(request(output=2000))
        await budget.settle(hold, [response(4000, 100)])
        self.assertEqual(budget.spent, 4100)
        with self.assertRaises(TokenBudgetExceeded) as caught:
            await budget.reserve(request(output=2000))
        self.assertEqual(caught.exception.diagnostics["reason"], "usage_exceeds_reservation")

    async def test_settlement_and_release_are_idempotent_per_hold(self):
        budget = self.budget(10000)
        first, _ = await budget.reserve(request(output=2000))
        second, _ = await budget.reserve(request(output=2000))
        await budget.settle(first, [response()])
        await budget.settle(first, [response()])
        await budget.release(first)
        self.assertEqual(budget.spent, 140)
        self.assertEqual(budget.reserved, 3166)
        await budget.release(second)
        await budget.release(second)
        self.assertEqual(budget.reserved, 0)

    async def test_holds_from_other_budget_cannot_release_capacity(self):
        one, two = self.budget(4000), self.budget(4000)
        hold, _ = await one.reserve(request(output=2000))
        with self.assertRaises(ValueError):
            await two.release(hold)
        self.assertEqual(one.reserved, 3166)

    async def test_observation_failure_cannot_interrupt_accounting(self):
        def record(_row):
            raise OSError("event log unavailable")
        budget = self.budget(4000, record=record)
        hold, _ = await budget.reserve(request(output=2000))
        await budget.settle(hold, [response()])
        self.assertEqual(budget.remaining, 3860)

    async def test_system_and_tool_schema_are_part_of_input_bound(self):
        budget = self.budget(4000)
        with self.assertRaises(TokenBudgetExceeded) as caught:
            await budget.reserve(request(output=1000, system=SystemMessage(content="s" * 2000),
                tools=[{"name": "probe", "description": "d" * 500}]))
        self.assertGreater(caught.exception.diagnostics["input_tokens_upper_bound"], 3500)
        self.assertEqual(budget.reserved, 0)


class PersistedSessionBudgetTests(SessionBudgetFixture):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "tokens" / "session.json"

    def persisted(self, tokens=10000, **kwargs):
        return self.budget(tokens, ledger_path=self.path, ledger_boundary=self.root, **kwargs)

    def ledger(self):
        return json.loads(self.path.read_text())

    async def test_reserve_settle_and_release_are_persisted_before_returning(self):
        budget = self.persisted()
        first, _ = await budget.reserve(request(output=2000))
        self.assertEqual(sum(self.ledger()["pending"].values()), 3166)
        await budget.settle(first, [response()])
        self.assertEqual(self.ledger()["spent_tokens"], 140)
        self.assertEqual(self.ledger()["pending"], {})
        second, _ = await budget.reserve(request(output=2000))
        await budget.release(second)
        self.assertEqual(self.ledger()["pending"], {})
        self.assertEqual(self.ledger()["spent_tokens"], 140)

    async def test_restored_known_spend_is_not_reset_by_new_instance(self):
        first = self.persisted()
        hold, _ = await first.reserve(request(output=2000))
        await first.settle(hold, [response()])
        first.close()
        restored = self.persisted()
        self.assertEqual(restored.spent, 140)
        self.assertEqual(restored.remaining, 9860)
        hold, _ = await restored.reserve(request(output=2000))
        await restored.settle(hold, [response()])
        self.assertEqual(restored.spent, 280)

    async def test_restart_charges_pending_hold_and_stops_on_uncertainty(self):
        first = self.persisted()
        await first.reserve(request(output=2000))
        first.close()
        restored = self.persisted()
        self.assertEqual(restored.spent, 3166)
        self.assertEqual(restored.reserved, 0)
        with self.assertRaises(TokenBudgetExceeded) as caught:
            await restored.reserve(request(output=2000))
        self.assertEqual(caught.exception.diagnostics["reason"], "usage_unknown")
        self.assertEqual(self.ledger()["spent_tokens"], 3166)
        self.assertEqual(self.ledger()["pending"], {})
        restored.close()
        again = self.persisted()
        self.assertEqual(again.spent, 3166)
        self.assertTrue(again.stopped)

    async def test_changed_limit_cannot_reset_existing_ledger(self):
        first = self.persisted()
        first.close()
        with self.assertRaises(ValueError):
            self.persisted(120000)
        self.assertEqual(self.ledger()["limit_tokens"], 10000)

    async def test_overlapping_instances_are_rejected_until_owner_closes(self):
        first = self.persisted()
        with self.assertRaises(ValueError):
            self.persisted()
        first.close()
        restored = self.persisted()
        hold, _ = await restored.reserve(request(output=2000))
        await restored.release(hold)

    async def test_corrupt_ledger_cannot_be_replaced_by_fresh_allowance(self):
        first = self.persisted()
        first.close()
        self.path.write_text("not json")
        with self.assertRaises(ValueError):
            self.persisted()
        self.assertEqual(self.path.read_text(), "not json")

    async def test_malformed_ledger_counters_and_schema_fail_closed(self):
        first = self.persisted()
        first.close()
        valid = self.ledger()
        for change in ({"spent_tokens": True}, {"spent_tokens": -1},
                {"pending": {"hold": -1}}, {"pending": {"hold": True}},
                {"schema_version": 9}, {"stopped": "false"},
                {"stop_reason": "usage_unknown", "stopped": False},
                {"stop_reason": [], "stopped": True}):
            with self.subTest(change=change):
                self.path.write_text(json.dumps({**valid, **change}))
                with self.assertRaises(ValueError):
                    self.persisted()

    async def test_persistence_error_stops_in_memory_admission(self):
        budget = self.persisted()
        self.path.unlink()
        self.path.mkdir()
        with self.assertRaises(TokenBudgetExceeded) as caught:
            await budget.reserve(request(output=2000))
        self.assertEqual(caught.exception.diagnostics["reason"], "ledger_write_failed")
        with self.assertRaises(TokenBudgetExceeded):
            await budget.reserve(request(output=2000))

    async def test_boundary_and_symlink_cannot_redirect_ledger(self):
        with self.assertRaises(ValueError):
            self.budget(10000, ledger_path=self.root.parent / "escaped-ledger.json", ledger_boundary=self.root)
        target = self.root / "target.json"
        target.write_text("{}")
        self.path.parent.mkdir()
        self.path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.persisted()
        self.assertEqual(target.read_text(), "{}")

    async def test_closed_owner_cannot_reuse_stale_in_memory_capacity(self):
        budget = self.persisted()
        budget.close()
        with self.assertRaises(TokenBudgetExceeded) as caught:
            await budget.reserve(request(output=2000))
        self.assertEqual(caught.exception.diagnostics["reason"], "budget_closed")


if __name__ == "__main__":
    unittest.main()
