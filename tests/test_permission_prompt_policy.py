"""Permission-mode assembly contracts, not OS access or model compliance tests."""

import json
import unittest

from nailong.core.prompts import build_prompt_parts


class PermissionPromptPolicyTests(unittest.TestCase):
    def parts(self, **overrides):
        arguments = {"project_root": "/example/project", "tool_names": {"read_file"}}
        arguments.update(overrides)
        return build_prompt_parts(**arguments)

    def test_omitted_mode_is_explicit_default_with_existing_boundaries(self):
        parts = self.parts()
        self.assertEqual(parts, self.parts(permission_mode="default"))
        prompt = parts["base_system"]
        self.assertIn("当前权限模式：default", prompt)
        self.assertIn("受保护内容", prompt)
        self.assertNotIn("当前权限模式：bypassPermissions", prompt)

    def test_bypass_replaces_project_blocklist_and_keeps_redaction(self):
        prompt = self.parts(permission_mode="bypassPermissions")["base_system"]
        self.assertIn("当前权限模式：bypassPermissions", prompt)
        self.assertIn("项目外路径", prompt)
        self.assertIn("原受保护路径", prompt)
        self.assertIn("密钥值必须脱敏", prompt)
        self.assertNotIn("不要主动寻找、读取或输出 API 密钥、.env 文件", prompt)
        self.assertNotIn("也不代表可以访问越界或受保护内容", prompt)

    def test_network_command_guidance_requires_current_command_capability(self):
        for mode in ("default", "bypassPermissions"):
            with self.subTest(mode=mode):
                prompt = self.parts(permission_mode=mode, tool_names={"read_file"})["base_system"]
                self.assertNotIn("run_command", prompt)
                command = self.parts(permission_mode=mode, tool_names={"run_command"})["base_system"]
                self.assertIn("## 网络与执行能力", command)
                self.assertIn("已注册的 run_command", command)

    def test_bypass_keeps_readonly_profile_boundaries_and_child_isolation(self):
        restrictions = {
            "review": "不得修改文件或运行命令",
            "plan": "不得修改文件、运行命令或调用子代理",
            "subagent": "不得修改文件、运行命令、请求审批或创建其他子代理",
        }
        for profile, restriction in restrictions.items():
            with self.subTest(profile=profile):
                parts = self.parts(profile=profile, permission_mode="bypassPermissions",
                                   fixed_memory="parent-memory", skill_catalog="parent-catalog")
                self.assertIn(restriction, parts["base_system"])
                self.assertNotIn("## 网络与执行能力", parts["base_system"])
                if profile == "subagent":
                    self.assertNotIn("只能读取和搜索项目文件", parts["base_system"])
                    self.assertEqual(parts["fixed_memory"], "")
                    self.assertEqual(parts["skill_catalog"], "")

    def test_init_bypass_preserves_output_target_without_mandatory_approval(self):
        prompt = self.parts(profile="init", permission_mode="bypassPermissions",
                            tool_names={"read_file", "write_file"})["base_system"]
        self.assertIn("最终只可写入 `.nailong/context.md`", prompt)
        self.assertNotIn("创建和覆盖都必须通过用户审批", prompt)
        self.assertNotIn("未获批准不得声称初始化完成", prompt)
        self.assertNotIn("不要读取密钥或虚拟环境内容", prompt)
        self.assertIn("写入成功后", prompt)

    def test_mode_changes_preserve_registered_capabilities_and_partitions(self):
        arguments = {"tool_names": {"read_file", "load_skill"},
                     "fixed_memory": "memory-body", "skill_catalog": "skill-entry"}
        default = self.parts(**arguments)
        bypass = self.parts(permission_mode="bypassPermissions", **arguments)
        for parts in (default, bypass):
            line = next(line for line in parts["base_system"].splitlines()
                        if line.startswith("当前可用工具："))
            self.assertEqual(json.loads(line.removeprefix("当前可用工具：")), ["load_skill", "read_file"])
        self.assertEqual(default["fixed_memory"], bypass["fixed_memory"])
        self.assertEqual(default["skill_catalog"], bypass["skill_catalog"])

    def test_other_supported_modes_are_named_and_unknown_mode_fails(self):
        for mode in ("plan", "acceptEdits", "accept_edits"):
            with self.subTest(mode=mode):
                self.assertIn(f"当前权限模式：{mode}", self.parts(permission_mode=mode)["base_system"])
        with self.assertRaises(ValueError):
            self.parts(permission_mode="unknown")


if __name__ == "__main__":
    unittest.main()
