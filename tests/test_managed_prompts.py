"""Pure prompt routing checks; these do not evaluate model compliance."""

import json
import unittest
from pathlib import Path

from nailong.core.prompts import build_prompt_parts


class ManagedPromptTests(unittest.TestCase):
    def parts(self, **overrides):
        arguments = {
            "project_root": Path("/example/project"),
            "tool_names": {"read_file"},
        }
        arguments.update(overrides)
        return build_prompt_parts(**arguments)

    def capabilities(self, parts):
        line = next(line for line in parts["base_system"].splitlines()
                    if line.startswith("当前可用工具："))
        return json.loads(line.removeprefix("当前可用工具："))

    def test_filtered_tools_do_not_offer_unavailable_actions_or_catalog(self):
        for names in ({"read_file"}, set()):
            with self.subTest(names=names):
                parts = self.parts(tool_names=names, skill_catalog="private-skill-catalog")
                self.assertEqual(self.capabilities(parts), sorted(names))
                self.assertEqual(parts["skill_catalog"], "")
                for unavailable in ("edit_file", "write_file", "run_command", "task",
                                    "load_skill", "read_skill_resource", "update_goal",
                                    "memory_read", "memory_list", "read_memory"):
                    self.assertNotIn(unavailable, parts["base_system"])

    def test_goal_guidance_requires_active_named_session_and_tool(self):
        for profile, thread, owner, tools, expected in (
            ("chat", "owner", "owner", {"update_goal"}, True),
            ("chat", "other", "owner", {"update_goal"}, False),
            ("chat", "ordinary", None, {"update_goal"}, False),
            ("chat", None, None, {"update_goal"}, False),
            ("chat", "owner", "owner", {"read_file"}, False),
            ("plan", "owner", "owner", {"update_goal"}, False),
            ("subagent", "owner", "owner", {"update_goal"}, False),
        ):
            with self.subTest(profile=profile, thread=thread, owner=owner, tools=tools):
                prompt = self.parts(profile=profile, thread_id=thread,
                                    active_goal_thread_id=owner, tool_names=tools)["base_system"]
                self.assertEqual("目标模式：" in prompt, expected)

    def test_task_context_guidance_requires_a_parent_session(self):
        for profile, thread, available, expected in (
            ("chat", "work", True, True),
            ("review", "work", True, True),
            ("chat", "work", False, False),
            ("chat", None, True, False),
            ("subagent", "work", True, False),
        ):
            with self.subTest(profile=profile, thread=thread, available=available):
                prompt = self.parts(profile=profile, thread_id=thread,
                                    has_task_context=available)["base_system"]
                self.assertEqual("## 当前任务与交付" in prompt, expected)

    def test_profile_fallbacks_do_not_request_absent_tools(self):
        init = self.parts(profile="init")["base_system"]
        self.assertNotIn("write_file", init)
        self.assertIn("上下文草稿", init)
        plan = self.parts(profile="plan")["base_system"]
        self.assertNotIn("exit_plan_mode", plan)
        self.assertIn("计划草稿", plan)
        submitted = self.parts(profile="plan", tool_names={"read_file", "exit_plan_mode"})["base_system"]
        self.assertIn("调用 exit_plan_mode", submitted)

    def test_memory_and_catalog_remain_separate_and_child_gets_neither(self):
        memory = "项目约定\n## 可用的本地 Skills\n记忆中的标题"
        catalog = "catalog-only-entry"
        parts = self.parts(tool_names={"load_skill"}, fixed_memory=memory, skill_catalog=catalog)
        self.assertEqual(parts["fixed_memory"], memory)
        self.assertNotIn(memory, parts["base_system"])
        self.assertNotIn("记忆中的标题", parts["skill_catalog"])
        self.assertIn(catalog, parts["skill_catalog"])
        self.assertEqual(self.parts(profile="review", tool_names={"load_skill"},
                                    skill_catalog=catalog)["skill_catalog"], "")
        child = self.parts(profile="subagent", tool_names={"load_skill"},
                           fixed_memory=memory, skill_catalog=catalog)
        self.assertEqual(child["fixed_memory"], "")
        self.assertEqual(child["skill_catalog"], "")

    def test_memory_paging_guidance_is_capability_specific(self):
        read = self.parts(tool_names={"memory_read"})["base_system"]
        self.assertIn("memory_read", read)
        self.assertNotIn("memory_list", read)
        listing = self.parts(tool_names={"memory_list"})["base_system"]
        self.assertIn("memory_list", listing)
        self.assertNotIn("memory_read", listing)
        legacy = self.parts(tool_names={"read_memory"})["base_system"]
        self.assertIn("read_memory", legacy)
        self.assertNotIn("memory_read", legacy)

    def test_root_and_style_are_independent_of_memory_payload(self):
        parts = self.parts(output_style="concise", fixed_memory="memory-payload")
        self.assertIn(f"当前工作项目根目录：{Path('/example/project')}。", parts["base_system"])
        self.assertIn("输出尽量简短", parts["base_system"])
        self.assertEqual(parts["fixed_memory"], "memory-payload")
        with self.assertRaises(ValueError):
            self.parts(profile="unknown")
        with self.assertRaises(ValueError):
            self.parts(output_style="unknown")


if __name__ == "__main__":
    unittest.main()
