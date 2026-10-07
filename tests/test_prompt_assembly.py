import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from agent import AgentRuntimeFactory
from config import Settings
from nailong.core.memory import load_project_memory
from nailong.core.sessions import ProjectSessionStore
from nailong.core.skills import SkillRegistry


class RecordingModel(FakeMessagesListChatModel):
    system_inputs: list[str] = []
    request_messages: list[list] = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.request_messages.append(list(messages))
        self.system_inputs.append("\n".join(
            str(message.content) for message in messages if message.type == "system"
        ))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class PromptAssemblyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="nailong-prompts-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "project"
        self.root.mkdir()
        memory = self.root / ".nailong" / "context.md"
        memory.parent.mkdir()
        self.memory_text = "项目约定：保留这个目录标题。\n\n## 可用的本地 Skills\n这是记忆中的示例标题。"
        memory.write_text(self.memory_text, encoding="utf-8")
        skill = self.root / ".agents" / "skills" / "probe" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: probe\ndescription: 检查示例文件\n---\n只读检查。\n", encoding="utf-8")
        self.model = RecordingModel(responses=[AIMessage(content="已返回回答")])
        settings = Settings("unit-test-key", "https://api.deepseek.com", "deepseek-chat", self.root)
        store = ProjectSessionStore(self.root, base_dir=Path(directory.name) / "data")
        # Keep real discovery and loading, while excluding personal filesystem data.
        with (
            patch("agent.ChatDeepSeek", return_value=self.model),
            patch("agent.SkillRegistry", side_effect=lambda root, **kwargs: SkillRegistry(
                root, user_root=Path(directory.name) / "user-skills", **kwargs
            )),
            patch("agent.load_project_memory", side_effect=lambda root, **kwargs: load_project_memory(
                root, user_file=Path(directory.name) / "user-memory.md", **kwargs
            )),
        ):
            self.factory = AgentRuntimeFactory(settings, session_store=store)
        self.addCleanup(self.factory.close)

    def ask(self, profile="chat", thread_id="normal", allowed_tools=None):
        runtime = self.factory(
            profile=profile,
            target_path=str(self.root) if profile == "review" else None,
            thread_id=thread_id,
            allowed_tools=allowed_tools,
        )
        runtime.invoke(
            {"messages": [HumanMessage(content="检查当前任务")]},
            {"configurable": {"thread_id": thread_id}},
            version="v2",
        )
        return self.model.system_inputs[-1]

    def capabilities(self, prompt):
        line = next((line for line in prompt.splitlines() if line.startswith("当前可用工具：")), None)
        self.assertIsNotNone(line, "模型请求没有说明实际可用工具")
        return json.loads(line.removeprefix("当前可用工具："))

    def test_filtered_runtime_sends_only_available_tool_guidance_to_model(self):
        for allowed in ({"read_file"}, set()):
            with self.subTest(allowed=allowed):
                prompt = self.ask(allowed_tools=allowed)
                self.assertEqual(self.capabilities(prompt), sorted(allowed))
                for unavailable in ("edit_file", "write_file", "run_command", "task", "load_skill", "read_skill_resource"):
                    self.assertNotIn(unavailable, prompt)
                self.assertNotIn("检查示例文件", prompt)

    def test_profile_capabilities_match_real_registered_tools(self):
        profiles = {
            "init": ["list_files", "read_file", "read_task_context", "read_tool_result", "write_file"],
            "review": ["list_files", "read_file", "read_task_context", "read_tool_result"],
            "plan": ["exit_plan_mode", "glob", "grep", "list_files", "load_skill", "memory_list", "memory_read", "read_file", "read_memory", "read_skill_resource", "read_task_context", "read_tool_result", "search_text"],
        }
        for profile, expected in profiles.items():
            with self.subTest(profile=profile):
                prompt = self.ask(profile, thread_id=profile)
                self.assertEqual(self.capabilities(prompt), expected)
                self.assertNotIn("edit_file", prompt)
                self.assertNotIn("run_command", prompt)
                self.assertNotIn("目标模式：", prompt)
        prompt = self.ask("plan", thread_id="filtered-plan", allowed_tools={"read_file"})
        self.assertEqual(self.capabilities(prompt), ["read_file"])
        self.assertNotIn("exit_plan_mode", prompt)
        self.assertNotIn("检查示例文件", prompt)

    def test_goal_guidance_follows_active_state_and_session_scope(self):
        self.assertNotIn("目标模式：", self.ask(thread_id="owner"))
        goal = self.factory.goal_store.create("修复并验证", thread_id="owner")
        self.assertIn("目标模式：", self.ask(thread_id="owner"))
        self.assertNotIn("目标模式：", self.ask(thread_id="other"))
        self.assertNotIn("目标模式：", self.ask(thread_id="owner", allowed_tools={"read_file"}))
        prompt = self.ask(thread_id="owner", allowed_tools={"update_goal"})
        self.assertIn("目标模式：", prompt)
        self.assertNotIn("run_command", prompt)
        self.factory.goal_store.update(goal.id, state="paused", thread_id="owner")
        self.assertNotIn("目标模式：", self.ask(thread_id="owner"))

    def test_context_report_does_not_parse_memory_content_as_catalog(self):
        parts = self.factory.context_parts("normal")
        self.assertIn(self.memory_text, parts["fixed_memory"])
        self.assertNotIn("这是记忆中的示例标题。", parts["skill_catalog"])
        self.assertIn("probe", parts["skill_catalog"])
        prompt = self.ask()
        self.assertEqual(prompt, parts["base_system"] + parts["fixed_memory"] + parts["skill_catalog"])

    def test_unbound_goal_does_not_activate_named_chat_until_attached(self):
        goal = self.factory.goal_store.create("恢复后继续处理")
        self.assertNotIn("目标模式：", self.factory.system_prompt(thread_id=None))
        self.assertNotIn("目标模式：", self.ask(thread_id="ordinary-chat"))
        self.factory.goal_store.attach_thread(goal.id, "goal-chat")
        self.assertIn("目标模式：", self.ask(thread_id="goal-chat"))
        self.assertNotIn("目标模式：", self.ask(thread_id="ordinary-chat"))

    def test_subagent_receives_readonly_capabilities_without_parent_memory(self):
        result = asyncio.run(self.factory._run_readonly_subagent("寻找入口文件"))
        self.assertEqual(result.text, "已返回回答")
        prompt = self.model.system_inputs[-1]
        self.assertEqual(self.capabilities(prompt), ["glob", "grep", "list_files", "read_file", "read_tool_result"])
        for unavailable in ("edit_file", "write_file", "run_command", "task", "load_skill", "read_skill_resource"):
            self.assertNotIn(unavailable, prompt)
        self.assertNotIn(self.memory_text, prompt)

    def test_restored_factory_keeps_one_current_task_projection_and_goal_binding(self):
        self.factory.task_store.begin("owner", "修复认证")
        self.factory.task_store.amend("owner", constraints=["保留公开接口"])
        self.factory.goal_store.create("修复认证", thread_id="owner")
        first = self.ask(thread_id="owner", allowed_tools={"read_file", "update_goal"})
        self.assertEqual(first.count("## 当前任务与交付"), 1)
        self.assertEqual(first.count("目标模式："), 1)

        settings = self.factory.settings
        store = self.factory.session_store
        registry = self.factory.skill_registry
        user_file = self.factory._memory_snapshot.store.user_file
        self.factory.close()
        with (
            patch("agent.ChatDeepSeek", return_value=self.model),
            patch("agent.SkillRegistry", return_value=registry),
            patch("agent.load_project_memory", side_effect=lambda root, **kwargs: load_project_memory(
                root, **{**kwargs, "user_file": user_file}
            )),
        ):
            self.factory = AgentRuntimeFactory(settings, session_store=store)
        self.addCleanup(self.factory.close)
        self.factory.task_store.amend("owner", constraints=["只读检查，禁止修改"])
        prompt = self.ask(thread_id="owner", allowed_tools={"read_file", "update_goal"})
        self.assertEqual(prompt.count("## 当前任务与交付"), 1)
        self.assertEqual(prompt.count("目标模式："), 1)
        messages = self.model.request_messages[-1]
        projections = [message for message in messages
                       if message.additional_kwargs.get("nailong_task_context")]
        self.assertEqual(len(projections), 1)
        self.assertIs(projections[0], messages[-1])
        self.assertIn("修复认证", projections[0].content)
        self.assertIn("只读检查，禁止修改", projections[0].content)
        self.assertNotIn("保留公开接口", "\n".join(str(message.content) for message in messages))
        state = self.factory(thread_id="owner", allowed_tools=set()).get_state(
            {"configurable": {"thread_id": "owner"}}
        )
        self.assertFalse(any(message.additional_kwargs.get("nailong_task_context")
                             for message in state.values["messages"]))
        other = self.ask(thread_id="other", allowed_tools={"read_file", "update_goal"})
        self.assertNotIn("## 当前任务与交付", other)
        self.assertNotIn("目标模式：", other)

    def test_child_parent_execution_identity_does_not_import_parent_task_or_history(self):
        from nailong.tools.execution import current_tool_execution

        self.factory.task_store.begin("owner", "父任务专属目标")
        self.factory.task_store.amend("owner", constraints=["父任务专属约束"])
        self.factory.goal_store.create("父任务专属目标", thread_id="owner")
        self.ask(thread_id="owner", allowed_tools={"read_file", "update_goal"})
        token = current_tool_execution.set({"thread_id": "owner"})
        try:
            result = asyncio.run(self.factory._run_readonly_subagent("寻找独立入口"))
        finally:
            current_tool_execution.reset(token)
        self.assertEqual(result.text, "已返回回答")
        messages = self.model.request_messages[-1]
        prompt = self.model.system_inputs[-1]
        self.assertNotIn("## 当前任务与交付", prompt)
        self.assertNotIn("目标模式：", prompt)
        self.assertFalse(any(message.additional_kwargs.get("nailong_task_context") for message in messages))
        full_request = "\n".join(str(message.content) for message in messages)
        for parent_content in ("父任务专属目标", "父任务专属约束", self.memory_text, "检查示例文件"):
            self.assertNotIn(parent_content, full_request)
        self.assertEqual([message.content for message in messages if message.type == "human"], ["寻找独立入口"])

    def test_topic_is_read_only_after_actual_model_tool_call(self):
        topic = self.root / '.nailong/memory/build.md'
        topic.parent.mkdir()
        topic.write_text('---\ndescription: 构建命令\n---\nTOPIC_CONTENT_FROM_TOOL', encoding='utf-8')
        self.model.responses = [AIMessage(content='', tool_calls=[{
            'name': 'memory_read', 'args': {'document': 'project/build.md'},
            'id': 'read-topic', 'type': 'tool_call'}]), AIMessage(content='done')]
        self.ask(thread_id='topic-reader')
        self.assertNotIn('TOPIC_CONTENT_FROM_TOOL', self.model.system_inputs[0])
        self.assertIn('project/build.md', self.model.system_inputs[0])
        tool_messages = [message for message in self.model.request_messages[-1] if message.type == 'tool']
        self.assertEqual(len(tool_messages), 1)
        result = json.loads(tool_messages[0].content)
        self.assertTrue(result['ok'], '真实工具必须注册并读到主题')
        self.assertIn('TOPIC_CONTENT_FROM_TOOL', result['content'])

    def test_next_runtime_refreshes_memory_and_preserves_user_store_boundary(self):
        self.assertIn(self.memory_text, self.ask(thread_id='before-change'))
        (self.root / '.nailong/context.md').write_text('UPDATED_CONTEXT', encoding='utf-8')
        prompt = self.ask(thread_id='after-change')
        self.assertIn('UPDATED_CONTEXT', prompt)
        self.assertNotIn(self.memory_text, prompt)
        self.assertTrue(self.factory._memory_snapshot.store.user_file.is_relative_to(self.root.parent.resolve()))

    def test_model_inputs_share_budget_across_multiple_reads_and_later_turns(self):
        from nailong.core.memory_context import estimate_memory_tokens
        topic = self.root / '.nailong/memory/large.md'
        topic.parent.mkdir()
        topic.write_text('长文本' * 5000, encoding='utf-8')
        calls = [{'name': 'memory_read', 'args': {'document': 'project/large.md', 'offset': offset},
                  'id': f'read-{offset}', 'type': 'tool_call'} for offset in (0, 4000, 8000)]
        self.model.responses = [AIMessage(content='', tool_calls=calls), AIMessage(content='done')]
        self.ask(thread_id='memory-budget')
        self.model.responses = [AIMessage(content='next turn')]
        self.model.i = 0
        self.ask(thread_id='memory-budget')
        for messages in self.model.request_messages:
            memory_text = self.factory._memory_snapshot.rendered_context
            tools = [message for message in messages if message.type == 'tool' and message.name in {'memory_list', 'memory_read'}]
            self.assertLessEqual(estimate_memory_tokens(memory_text) +
                                 sum(estimate_memory_tokens(message.content) for message in tools), 4000)


if __name__ == "__main__":
    unittest.main()
