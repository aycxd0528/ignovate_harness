"""Delegation routing checks; these do not measure model decisions or budgets."""

import json
import unittest

from nailong.core.prompts import DELEGATION_POLICY, build_prompt_parts


class SubagentPromptPolicyTests(unittest.TestCase):
    def parts(self, *, profile="chat", tools=(), **overrides):
        return build_prompt_parts(
            project_root="/example/project", profile=profile,
            tool_names=set(tools), **overrides,
        )

    def test_task_tool_presence_cannot_override_non_chat_profiles(self):
        for profile in ("init", "review", "plan", "subagent"):
            with self.subTest(profile=profile):
                prompt = self.parts(profile=profile, tools={"task", "read_file"})["base_system"]
                self.assertNotIn(DELEGATION_POLICY, prompt)

    def test_chat_receives_one_delegation_policy_only_with_actual_task_tool(self):
        offered = self.parts(tools={"task", "read_file"})["base_system"]
        self.assertEqual(offered.count(DELEGATION_POLICY), 1)
        capabilities = next(line for line in offered.splitlines()
                            if line.startswith("当前可用工具："))
        self.assertEqual(json.loads(capabilities.removeprefix("当前可用工具：")), ["read_file", "task"])
        for tools in (set(), {"read_file"}, {"grep", "read_file"}):
            with self.subTest(tools=tools):
                prompt = self.parts(tools=tools)["base_system"]
                self.assertNotIn(DELEGATION_POLICY, prompt)

    def test_filtering_task_keeps_project_reading_and_memory_partitions(self):
        parts = self.parts(tools={"read_file", "grep"}, fixed_memory="project-memory")
        self.assertNotIn(DELEGATION_POLICY, parts["base_system"])
        self.assertIn("## 项目探索", parts["base_system"])
        self.assertEqual(parts["fixed_memory"], "project-memory")
        self.assertEqual(parts["skill_catalog"], "")

    def test_child_does_not_inherit_parent_delegation_task_goal_or_memory(self):
        parts = self.parts(
            profile="subagent", tools={"read_file", "task"},
            thread_id="parent", has_task_context=True, active_goal_thread_id="parent",
            fixed_memory="parent-memory", skill_catalog="parent-skill",
        )
        self.assertNotIn(DELEGATION_POLICY, parts["base_system"])
        self.assertNotIn("## 当前任务与交付", parts["base_system"])
        self.assertNotIn("目标模式：", parts["base_system"])
        self.assertEqual(parts["fixed_memory"], "")
        self.assertEqual(parts["skill_catalog"], "")


if __name__ == "__main__":
    unittest.main()
