import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage

from nailong.core.plan import PlanStore
from nailong.core.compact import compact_messages
from nailong.core.commands import CommandRegistry
from nailong.core.memory import load_project_memory
from nailong.core.hooks import HookRunner
from nailong.core.goal import GoalStore
from nailong.core.costs import CostEstimator
from nailong.tools.agents import ReadOnlyTaskRunner
from nailong.tools.files import FileSession
from nailong.tools.registry import build_tool_specs


class PlanStoreTests(unittest.TestCase):
    def test_plan_is_staged_in_memory_then_written_only_after_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            store = PlanStore(root, api_key="secret-key")
            plan_id = store.stage("# Plan\nDo the work. secret-key")

            self.assertFalse((root / ".nailong" / "plans").exists())
            self.assertNotIn("secret-key", store.get(plan_id))
            approved = store.approve(plan_id)

            self.assertTrue(approved.path.is_relative_to(root))
            self.assertEqual(approved.path.read_text(encoding="utf-8"), approved.markdown)
            self.assertEqual(approved.path.stat().st_mode & 0o777, 0o600)
            self.assertIsNone(store.get(plan_id))

    def test_plan_approval_rejects_unknown_or_empty_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(directory)
            with self.assertRaises(ValueError):
                store.stage("  ")
            with self.assertRaises(KeyError):
                store.approve("missing")

    def test_plan_output_rejects_a_symlink_to_internal_or_external_directories(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory).resolve()
            (root / ".nailong").mkdir()
            (root / ".nailong" / "plans").symlink_to(Path(outside), target_is_directory=True)
            store = PlanStore(root)
            plan_id = store.stage("# plan")
            with self.assertRaises(ValueError):
                store.approve(plan_id)


class CompactionTests(unittest.IsolatedAsyncioTestCase):
    def test_compaction_pins_system_plan_memory_and_recent_turns(self):
        messages = [
            SystemMessage(content="system prompt"),
            SystemMessage(content="approved plan", additional_kwargs={"nailong_pin": "plan"}),
            SystemMessage(content="project memory", additional_kwargs={"nailong_pin": "memory"}),
            HumanMessage(content="old request", id="old-request"),
            ToolMessage(content='{"ok":true,"path":"src/a.py","content":"' + ("x" * 2000) + '"}', id="old-tool", tool_call_id="r1", name="read_file"),
            AIMessage(content="old answer", id="old-answer"),
            HumanMessage(content="active goal objective", additional_kwargs={"nailong_pin": "goal"}),
            HumanMessage(content="recent request"),
            AIMessage(content="recent answer"),
        ]

        result = compact_messages(messages, keep_turns=1, min_gain=0.01)

        contents = [str(message.content) for message in result.messages]
        self.assertIn("system prompt", contents)
        self.assertIn("approved plan", contents)
        self.assertIn("project memory", contents)
        self.assertIn("active goal objective", contents)
        self.assertIn("recent request", contents)
        self.assertIn("recent answer", contents)
        self.assertNotIn('"content"', "\n".join(contents))
        self.assertTrue(result.compacted)
        self.assertEqual(len(result.removed_ids), 3)

    async def test_service_applies_l2_replacements_without_touching_external_tool_snapshot(self):
        from agent_service import AgentService

        messages = [
            SystemMessage(content="system", id="system"),
            HumanMessage(content="old task", id="old-user"),
            ToolMessage(content='{"ok":true,"content":"' + ("x" * 2000) + '"}', id="read", name="read_file", tool_call_id="read-call"),
            AIMessage(content="old answer", id="old-answer"),
            HumanMessage(content="task two", id="user-two"),
            AIMessage(content="answer two", id="answer-two"),
            HumanMessage(content="task three", id="user-three"),
            AIMessage(content="answer three", id="answer-three"),
            HumanMessage(content="task four", id="user-four"),
            AIMessage(content="answer four", id="answer-four"),
            HumanMessage(content="current task", id="current-user"),
            AIMessage(content="current answer", id="current-answer"),
        ]

        class Agent:
            async def aget_state(self, _config):
                return type("State", (), {"values": {"messages": list(messages)}})()

            async def aupdate_state(self, _config, update):
                from langgraph.graph.message import add_messages
                messages[:] = add_messages(messages, update['messages'])

        agent = Agent()

        class Factory:
            settings = type("Settings", (), {"project_root": Path(tempfile.gettempdir())})()

            async def async_runtime(self, **_kwargs):
                return agent

        service = AgentService(Factory())
        result = await service.compact_context("compact-thread")

        self.assertTrue(result["compacted"])
        self.assertIn("current task", [str(item.content) for item in messages])
        self.assertTrue(any(str(item.content).startswith("较早对话摘要") for item in messages))
        self.assertFalse(any(getattr(item, "id", None) == "read" for item in messages))

    def test_compaction_skips_when_savings_are_below_twenty_percent(self):
        result = compact_messages(
            [SystemMessage(content="system"), HumanMessage(content="old"), AIMessage(content="a"), HumanMessage(content="q"), AIMessage(content="answer")],
            keep_turns=1,
        )
        self.assertFalse(result.compacted)
        self.assertEqual(result.reason, "insufficient_savings")


class CommandAndMemoryTests(unittest.TestCase):
    def test_command_rejects_symlink_swapped_after_path_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            commands = root / '.nailong' / 'commands'
            commands.mkdir(parents=True)
            target = commands / 'probe.md'
            target.write_text('public prompt')
            secret = root / '.env'
            secret.write_text('synthetic-protected-sentinel')
            registry = CommandRegistry(root, user_root=root/'user', builtins_root=root/'builtins')
            parse = registry._parse
            def swapped(path):
                target.unlink()
                target.symlink_to(secret)
                return parse(path)
            with patch.object(registry, '_parse', side_effect=swapped):
                self.assertIsNone(registry.resolve('probe', [], available_tools=set()))

    def test_project_command_cannot_expand_allowed_tools_and_substitutes_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / ".nailong" / "commands"
            path.mkdir(parents=True)
            (path / "review.md").write_text(
                "---\ndescription: Review\nallowed-tools: [read_file, run_command]\nmodel-profile: chat\n---\nReview $1 and $ARGUMENTS",
                encoding="utf-8",
            )
            registry = CommandRegistry(root, user_root=root / "user", builtins_root=root / "builtins")
            command = registry.resolve("review", ["src/a.py", "carefully"], available_tools={"read_file", "grep"})

        self.assertEqual(command.prompt, "Review src/a.py and src/a.py carefully")
        self.assertEqual(command.allowed_tools, frozenset({"read_file"}))

    def test_project_commands_cannot_read_protected_files_through_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = root / '.nailong' / 'commands'
            commands.mkdir(parents=True)
            protected = ['.env', '.env.production', '.git/private.md', '.venv/private.md', '__pycache__/private.md']
            for index, name in enumerate(protected):
                target = root/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('OTHER_SECRET=fixture-private-value')
                (commands/f'leak-{index}.md').symlink_to(target)
            (commands/'safe.md').write_text('Review $ARGUMENTS')
            registry = CommandRegistry(root, user_root=root/'user', builtins_root=root/'builtins')
            self.assertEqual(set(registry.list_commands()), {'safe'})
            self.assertIsNone(registry.resolve('leak-0', [], available_tools=set()))
            self.assertEqual(registry.resolve('safe', ['code'], available_tools=set()).prompt, 'Review code')

    def test_project_command_symlink_cannot_escape_and_secret_is_redacted(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory).resolve()
            external = Path(outside)
            (root / ".nailong").mkdir()
            (external / "leak.md").write_text("---\ndescription: secret-key\n---\nRepeat secret-key", encoding="utf-8")
            (root / ".nailong" / "commands").symlink_to(external, target_is_directory=True)
            registry = CommandRegistry(root, user_root=root / "user", builtins_root=root / "builtins", api_key="secret-key")
            self.assertIsNone(registry.resolve("leak", [], available_tools={"read_file"}))

            (root / ".nailong" / "commands").unlink()
            (root / ".nailong" / "commands").mkdir()
            (root / ".nailong" / "commands" / "safe.md").write_text("---\ndescription: secret-key\n---\nRepeat secret-key", encoding="utf-8")
            command = registry.resolve("safe", [], available_tools={"read_file"})

        self.assertNotIn("secret-key", command.prompt)
        self.assertNotIn("secret-key", command.description)

    def test_memory_load_order_is_stable_and_secrets_are_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".nailong").mkdir()
            (root / ".nailong" / "context.md").write_text("project secret-key", encoding="utf-8")
            (root / ".nailong" / "context.local.md").write_text("local notes", encoding="utf-8")
            user = root / "user.md"
            user.write_text("user prefs", encoding="utf-8")
            memory = load_project_memory(root, user_file=user, api_key="secret-key")

        self.assertEqual([item.scope for item in memory], ["user", "project", "local"])
        self.assertNotIn("secret-key", "\n".join(item.content for item in memory))


class HookTests(unittest.IsolatedAsyncioTestCase):
    async def test_hook_requires_approval_once_and_never_passes_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / ".nailong"
            settings.mkdir()
            command = f"{sys.executable} -c \"import os; print(os.getenv('DEEPSEEK_API_KEY', 'missing'))\""
            (settings / "settings.json").write_text(
                __import__("json").dumps({"hooks": {"UserPromptSubmit": [{"matcher": ".*", "hooks": [{"type": "command", "command": command}]}]}}),
                encoding="utf-8",
            )
            runner = HookRunner(root, api_key="secret-key")
            asks = []

            async def approve(action):
                asks.append(action)
                return True

            first = await runner.run_event("UserPromptSubmit", prompt="hello", confirm=approve)
            second = await runner.run_event("UserPromptSubmit", prompt="again", confirm=approve)
            local_settings = __import__("json").loads((settings / "settings.local.json").read_text(encoding="utf-8"))

        self.assertEqual(first.outputs[0].stdout.strip(), "missing")
        self.assertEqual(second.outputs[0].stdout.strip(), "missing")
        self.assertEqual(len(asks), 1)
        self.assertNotIn(command, str(local_settings))

    async def test_rejected_or_exit_two_blocking_hook_prevents_following_action(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / ".nailong"
            settings.mkdir()
            command = f"{sys.executable} -c 'import sys; print(\"blocked\"); sys.exit(2)'"
            (settings / "settings.json").write_text(
                __import__("json").dumps({"hooks": {"PreToolUse": [{"matcher": "write_file", "hooks": [{"type": "command", "command": command}]}]}}),
                encoding="utf-8",
            )
            runner = HookRunner(root)
            denied = await runner.run_event("PreToolUse", tool_name="write_file", confirm=lambda _action: False)
            allowed = await runner.run_event("PreToolUse", tool_name="write_file", confirm=lambda _action: True)

        self.assertTrue(denied.blocked)
        self.assertTrue(allowed.blocked)
        self.assertEqual(allowed.outputs[0].stdout.strip(), "blocked")


class TaskRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_runner_caps_parallelism_and_spends_parent_budget(self):
        active = 0
        maximum = 0

        async def worker(prompt):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return "result " + prompt

        runner = ReadOnlyTaskRunner(worker, max_concurrency=3, token_budget=1000, child_output_tokens=10)
        results = await asyncio.gather(*(runner.run("thread", f"task {index}") for index in range(6)))

        self.assertEqual(maximum, 3)
        self.assertTrue(all(item.startswith("result") for item in results))
        self.assertLess(runner.remaining_budget("thread"), 1000)

    async def test_task_runner_refuses_when_budget_is_exhausted(self):
        runner = ReadOnlyTaskRunner(lambda _prompt: asyncio.sleep(0, result="x"), token_budget=1)
        result = await runner.run("thread", "a long research task")
        self.assertIn("预算", result)


class GoalStoreTests(unittest.TestCase):
    def test_goal_persists_across_store_instances_and_redacts_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "goals.json"
            first = GoalStore(path, api_key="secret-key")
            goal = first.create("Fix secret-key issue", max_rounds=4, max_cost_usd=0.5)
            second = GoalStore(path, api_key="secret-key")
            restored = second.get(goal.id)
            persisted = path.read_text(encoding="utf-8")

        self.assertEqual(restored.objective, "Fix [密钥已隐藏] issue")
        self.assertEqual(restored.state, "active")
        self.assertNotIn("secret-key", persisted)

    def test_goal_requires_completion_evidence_and_same_blocker_three_rounds(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory) / "goals.json")
            goal = store.create("Fix issue")
            complete, reason, _ = store.update(goal.id, state="complete")
            self.assertFalse(complete)
            self.assertIn("实际执行", reason)

            for expected in ("1/3", "2/3"):
                complete, reason, _ = store.update(goal.id, state="blocked", blocker="missing dependency")
                self.assertFalse(complete)
                self.assertIn(expected, reason)
                store.record_round(goal.id, cost_usd=0.01, files_changed=False, tool_calls=1)
            complete, _, blocked = store.update(goal.id, state="blocked", blocker="missing dependency")
            self.assertTrue(complete)
            self.assertEqual(blocked.state, "blocked")

    def test_completion_requires_a_successful_recorded_command_not_model_supplied_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "goals.json"
            store = GoalStore(path, api_key="private-key")
            goal = store.create("Fix issue", thread_id="goal-thread")
            with self.assertRaises(TypeError):
                store.update(
                    goal.id,
                    state="complete",
                    verification_command="pytest -q",
                    verification_output="1 passed",
                )
            store.record_verification(
                "pytest -q",
                {"ok": False, "exit_code": 1, "output": "1 failed"},
                thread_id="goal-thread",
            )
            complete, reason, _ = store.update(
                goal.id,
                state="complete",
                thread_id="goal-thread",
            )
            self.assertFalse(complete)
            self.assertIn("成功实际执行", reason)

            store.record_verification(
                "pytest -q",
                {"ok": True, "exit_code": 0, "output": "1 passed private-key"},
                thread_id="goal-thread",
            )
            reloaded = GoalStore(path, api_key="private-key")
            complete, _, finished = reloaded.update(
                goal.id,
                state="complete",
                thread_id="goal-thread",
            )
            persisted = path.read_text(encoding="utf-8")

        self.assertTrue(complete)
        self.assertEqual(finished.verification_command, "pytest -q")
        self.assertIn("退出码: 0", finished.verification_output)
        self.assertIn("1 passed", finished.verification_output)
        self.assertNotIn("private-key", finished.verification_output)
        self.assertNotIn("private-key", persisted)

    def test_file_mutation_invalidates_recorded_goal_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory) / "goals.json")
            goal = store.create("Fix issue", thread_id="goal-thread")
            store.record_verification(
                "pytest -q",
                {"ok": True, "exit_code": 0, "output": "1 passed"},
                thread_id="goal-thread",
            )
            store.invalidate_verification(thread_id="goal-thread")
            complete, reason, _ = store.update(goal.id, state="complete", thread_id="goal-thread")

        self.assertFalse(complete)
        self.assertIn("实际执行", reason)

    def test_goal_thread_ownership_blocks_other_sessions_from_claiming_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory) / "goals.json")
            goal = store.create("Fix issue", thread_id="goal-thread")
            recorded = store.record_verification(
                "pytest -q",
                {"ok": True, "exit_code": 0, "output": "1 passed"},
                thread_id="other-thread",
            )
            complete, reason, _ = store.update(goal.id, state="complete", thread_id="other-thread")

        self.assertFalse(recorded)
        self.assertFalse(complete)
        self.assertIn("会话", reason)

    def test_tool_schema_does_not_accept_self_reported_verification_output(self):
        with tempfile.TemporaryDirectory() as directory:
            specs = {spec.name: spec for spec in build_tool_specs(session=FileSession(Path(directory)))}
        properties = specs["update_goal"].input_schema["properties"]
        self.assertNotIn("verification_command", properties)
        self.assertNotIn("verification_output", properties)

    def test_registry_records_real_command_result_and_invalidates_it_after_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = GoalStore(root / ".nailong" / "goals.json")
            goal = store.create("Fix issue", thread_id="goal-thread")
            specs = {
                spec.name: spec
                for spec in build_tool_specs(
                    session=FileSession(root),
                    goal_store=store,
                    thread_id="goal-thread",
                )
            }
            with patch(
                "nailong.tools.registry.local_tools.run_command",
                return_value={"ok": True, "exit_code": 0, "output": "2 passed", "timed_out": False},
            ):
                result = specs["run_command"].handler("pytest -q")
            self.assertTrue(result["ok"])
            completed = specs["update_goal"].handler("complete")
            finished = store.get(goal.id)

        self.assertTrue(completed["ok"])
        self.assertEqual(completed["state"], "complete")
        self.assertEqual(finished.verification_command, "pytest -q")
        self.assertIn("2 passed", finished.verification_output)

    def test_goal_pauses_at_cost_round_and_idle_guardrails(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory) / "goals.json")
            cost_goal = store.create("cost guard", max_rounds=5, max_cost_usd=0.02)
            cost_state = store.record_round(cost_goal.id, cost_usd=0.02, files_changed=True, tool_calls=1)
            self.assertEqual(cost_state.state, "paused")
            self.assertIn("成本上限", cost_state.pause_reason)

            idle_goal = store.create("idle guard", max_rounds=5, max_cost_usd=1)
            store.record_round(idle_goal.id, cost_usd=0, files_changed=False, tool_calls=0)
            idle_state = store.record_round(idle_goal.id, cost_usd=0, files_changed=False, tool_calls=0)
            self.assertEqual(idle_state.state, "paused")

            round_goal = store.create("round guard", max_rounds=1, max_cost_usd=1)
            round_state = store.record_round(round_goal.id, cost_usd=0, files_changed=True, tool_calls=1)
            self.assertEqual(round_state.state, "paused")

    def test_unattended_approval_pauses_goal(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory) / "goals.json")
            goal = store.create("approval guard")
            state = store.record_round(
                goal.id,
                cost_usd=0.001,
                files_changed=False,
                tool_calls=1,
                unattended_approval=True,
            )
        self.assertEqual(state.state, "paused")
        self.assertIn("审批", state.pause_reason)

    def test_paused_goal_resumes_explicitly_on_the_current_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory) / "goals.json")
            goal = store.create("approval guard", thread_id="old-thread")
            store.record_round(
                goal.id,
                cost_usd=0.001,
                files_changed=False,
                tool_calls=1,
                unattended_approval=True,
            )
            resumed, message, current = store.resume(goal.id, thread_id="resumed-thread")
            old_thread_result = store.record_verification(
                "pytest -q",
                {"ok": True, "exit_code": 0, "output": "1 passed"},
                thread_id="old-thread",
            )
            new_thread_result = store.record_verification(
                "pytest -q",
                {"ok": True, "exit_code": 0, "output": "1 passed"},
                thread_id="resumed-thread",
            )

        self.assertTrue(resumed, message)
        self.assertEqual(current.state, "active")
        self.assertEqual(current.thread_id, "resumed-thread")
        self.assertFalse(old_thread_result)
        self.assertTrue(new_thread_result)

    def test_unattended_approval_overrides_same_round_complete_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory) / "goals.json")
            goal = store.create("approval guard")
            store.record_verification("pytest -q", {"ok": True, "exit_code": 0, "output": "pass"})
            store.update(goal.id, state="complete")
            state = store.record_round(
                goal.id,
                cost_usd=0.001,
                files_changed=True,
                tool_calls=2,
                unattended_approval=True,
            )

        self.assertEqual(state.state, "paused")
        self.assertIn("审批", state.pause_reason)


class CostEstimatorTests(unittest.TestCase):
    def test_estimator_uses_cache_tokens_and_conservative_peak_rates(self):
        with tempfile.TemporaryDirectory() as directory:
            estimator = CostEstimator("deepseek-flash", directory)
            cost = estimator.estimate({"input_tokens": 1_000_000, "cache_hit_tokens": 500_000, "output_tokens": 100_000})
        self.assertAlmostEqual(cost, 0.273, places=6)

    def test_unknown_model_requires_project_pricing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".nailong").mkdir()
            (root / ".nailong" / "settings.json").write_text(
                __import__("json").dumps({"pricing": {"private-model": {"input_per_million": 1, "cache_hit_per_million": 0.2, "output_per_million": 2}}}),
                encoding="utf-8",
            )
            estimator = CostEstimator("private-model", root)
            self.assertEqual(estimator.estimate({"input_tokens": 10, "output_tokens": 10}), 0.00003)

        with tempfile.TemporaryDirectory() as directory:
            estimator = CostEstimator("unknown-model", directory)
            self.assertFalse(estimator.available)
            with self.assertRaises(ValueError):
                estimator.estimate({})


class RegistryProfileTests(unittest.TestCase):
    def test_plan_profile_exposes_only_read_tools_and_exit_plan_mode(self):
        specs = build_tool_specs(profile="plan")
        by_name = {spec.name: spec for spec in specs}
        self.assertIn("exit_plan_mode", by_name)
        self.assertTrue(all(spec.read_only or spec.name == "exit_plan_mode" for spec in specs))
        self.assertNotIn("run_command", by_name)

    def test_subagent_profile_is_read_only_and_has_no_recursive_task_tool(self):
        specs = build_tool_specs(profile="subagent")
        self.assertTrue(specs)
        self.assertTrue(all(spec.read_only for spec in specs))
        self.assertNotIn("task", {spec.name for spec in specs})

    def test_custom_allowed_tools_only_removes_tools(self):
        specs = build_tool_specs(profile="chat", allowed_tools={"read_file", "run_command"})
        self.assertEqual({spec.name for spec in specs}, {"read_file", "run_command"})


if __name__ == "__main__":
    unittest.main()
