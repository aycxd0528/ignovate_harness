import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from config import Settings
from agent import AgentRuntimeFactory
from agent_service import AgentService
from nailong.core.sessions import ProjectSessionStore
import local_tools


_TEST_DATA_DIRECTORY = None
_ORIGINAL_DATA_DIRECTORY = None


def setUpModule():
    global _TEST_DATA_DIRECTORY, _ORIGINAL_DATA_DIRECTORY
    _ORIGINAL_DATA_DIRECTORY = os.environ.get("NAILONG_DATA_DIR")
    _TEST_DATA_DIRECTORY = tempfile.TemporaryDirectory(prefix="nailong-agent-tests-")
    os.environ["NAILONG_DATA_DIR"] = _TEST_DATA_DIRECTORY.name


def tearDownModule():
    if _ORIGINAL_DATA_DIRECTORY is None:
        os.environ.pop("NAILONG_DATA_DIR", None)
    else:
        os.environ["NAILONG_DATA_DIR"] = _ORIGINAL_DATA_DIRECTORY
    if _TEST_DATA_DIRECTORY is not None:
        _TEST_DATA_DIRECTORY.cleanup()


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def import_agent(test_case):
    try:
        return importlib.import_module("agent")
    except ModuleNotFoundError as error:
        test_case.fail(f"agent module is missing: {error}")


def make_settings(project_root=None):
    return Settings(
        api_key="unit-test-key",
        api_base="https://api.deepseek.com",
        model="deepseek-chat",
        project_root=Path(project_root) if project_root is not None else Path(
            tempfile.mkdtemp(prefix='project-', dir=_TEST_DATA_DIRECTORY.name if _TEST_DATA_DIRECTORY else None)),
    )


def write_request():
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_file",
                "args": {"path": "hello.txt", "content": "hello"},
                "id": "write-1",
            }
        ],
    )


def context_write_request(content="项目上下文"):
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_file",
                "args": {
                    "path": ".nailong/context.md",
                    "content": content,
                },
                "id": f"context-write-{content}",
            }
        ],
    )


def multiple_requests():
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "run_command",
                "args": {"command": "pwd"},
                "id": "command-1",
            },
            {
                "name": "write_file",
                "args": {"path": "hello.txt", "content": "hello"},
                "id": "write-2",
            },
        ],
    )


def file_read_request():
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_file",
                "args": {"path": "hello.txt"},
                "id": "read-1",
            }
        ],
    )


def edit_request():
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "edit_file",
                "args": {
                    "path": "hello.txt",
                    "old_string": "before",
                    "new_string": "after",
                },
                "id": "edit-1",
            }
        ],
    )


class AgentRuntimeTests(unittest.TestCase):
    def test_async_session_restores_in_a_separate_process(self):
        write_turn = """
import asyncio, sys
from pathlib import Path
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from config import Settings
from agent import AgentRuntimeFactory
from agent_service import AgentService
class FakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs): return self
async def main():
    root = Path(sys.argv[1])
    settings = Settings('unit-test-key', 'https://api.deepseek.com', 'deepseek-chat', root)
    factory = AgentRuntimeFactory(settings)
    factory.model = FakeModel(responses=[AIMessage(content='子进程已保存')])
    service = AgentService(factory, api_key=settings.api_key)
    answer = await service.run_turn('跨进程恢复问题', {'configurable': {'thread_id': 'process-restore'}, 'recursion_limit': 40})
    print(answer)
    await factory.aclose()
    factory.close()
asyncio.run(main())
"""
        read_turn = """
import asyncio, json, sys
from pathlib import Path
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from config import Settings
from agent import AgentRuntimeFactory
async def main():
    root = Path(sys.argv[1])
    settings = Settings('unit-test-key', 'https://api.deepseek.com', 'deepseek-chat', root)
    factory = AgentRuntimeFactory(settings)
    runtime = await factory.async_runtime(thread_id='process-restore')
    state = await runtime.aget_state({'configurable': {'thread_id': 'process-restore'}, 'recursion_limit': 40})
    print(json.dumps([message.content for message in state.values['messages']], ensure_ascii=False))
    await factory.aclose()
    factory.close()
asyncio.run(main())
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            data_root = Path(directory) / "agent-data"
            child_env = {**os.environ, "NAILONG_DATA_DIR": str(data_root)}
            first = subprocess.run(
                [sys.executable, "-c", write_turn, str(root)],
                check=True,
                capture_output=True,
                text=True,
                env=child_env,
                cwd=Path(__file__).resolve().parents[1],
            )
            second = subprocess.run(
                [sys.executable, "-c", read_turn, str(root)],
                check=True,
                capture_output=True,
                text=True,
                env=child_env,
                cwd=Path(__file__).resolve().parents[1],
            )

        self.assertEqual(first.stdout.strip(), "子进程已保存")
        self.assertEqual(
            second.stdout.strip(),
            '["跨进程恢复问题", "子进程已保存"]',
        )

    def test_runtime_prompt_names_selected_project_root(self):
        settings = make_settings()
        with (
            patch("agent.ChatDeepSeek", return_value=object()),
            patch("agent.create_agent", return_value=object()) as create_agent,
        ):
            AgentRuntimeFactory(settings)()

        self.assertIn(str(settings.project_root), create_agent.call_args.kwargs["system_prompt"])

    def test_runtime_constructs_deepseek_and_keeps_thread_history(self):
        agent_module = import_agent(self)
        fake_model = ToolCallingFakeModel(
            responses=[AIMessage(content="第一轮"), AIMessage(content="第二轮")]
        )
        with patch("agent.ChatDeepSeek", return_value=fake_model) as model_factory:
            runtime = agent_module.create_agent_runtime(make_settings())

        model_factory.assert_called_once_with(
            model="deepseek-chat",
            api_key="unit-test-key",
            base_url="https://api.deepseek.com",
            timeout=30,
            max_retries=2,
        )
        config = {"configurable": {"thread_id": "history-test"}, "recursion_limit": 40}
        first = runtime.invoke(
            {"messages": [HumanMessage(content="你好")]}, config, version="v2"
        )
        second = runtime.invoke(
            {"messages": [HumanMessage(content="继续")]}, config, version="v2"
        )

        self.assertEqual(first.value["messages"][-1].content, "第一轮")
        self.assertEqual(second.value["messages"][-1].content, "第二轮")
        self.assertEqual(len(second.value["messages"]), 4)

    def test_sqlite_checkpointer_restores_state_in_a_new_factory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            settings = make_settings(root)
            store = ProjectSessionStore(root, base_dir=Path(directory) / "agent-data")
            first_model = ToolCallingFakeModel(responses=[AIMessage(content="持久化答复")])
            with patch("agent.ChatDeepSeek", return_value=first_model):
                first_factory = AgentRuntimeFactory(settings, session_store=store)
            config = {"configurable": {"thread_id": "persisted-thread"}, "recursion_limit": 40}
            first_factory().invoke(
                {"messages": [HumanMessage(content="先保存这段对话")]},
                config,
                version="v2",
            )
            first_factory.close()

            second_model = ToolCallingFakeModel(responses=[AIMessage(content="恢复后继续")])
            with patch("agent.ChatDeepSeek", return_value=second_model):
                second_factory = AgentRuntimeFactory(
                    settings,
                    session_store=ProjectSessionStore(root, base_dir=Path(directory) / "agent-data"),
                )
            restored = second_factory().get_state(config)
            self.assertEqual(
                [message.content for message in restored.values["messages"]],
                ["先保存这段对话", "持久化答复"],
            )
            continued = second_factory().invoke(
                {"messages": [HumanMessage(content="继续")]}, config, version="v2"
            )
            self.assertEqual(continued.value["messages"][-1].content, "恢复后继续")
            second_factory.close()

    def test_model_response_secret_is_filtered_before_sqlite_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            settings = make_settings(root)
            secret = "secret-in-model-output"
            settings = Settings(secret, settings.api_base, settings.model, root)
            store = ProjectSessionStore(root, base_dir=Path(directory) / "state", api_key=secret)
            model = ToolCallingFakeModel(responses=[AIMessage(content=f"echo {secret}")])
            with patch("agent.ChatDeepSeek", return_value=model):
                factory = AgentRuntimeFactory(settings, session_store=store)
            runtime = factory()
            config = {"configurable": {"thread_id": "redacted-checkpoint"}, "recursion_limit": 40}
            runtime.invoke({"messages": [HumanMessage(content="hello")]}, config, version="v2")
            state_text = runtime.get_state(config).values["messages"][-1].content
            database_bytes = store.database_path.read_bytes()
            factory.close()

        self.assertEqual(state_text, "echo [密钥已隐藏]")
        self.assertNotIn(secret.encode("utf-8"), database_bytes)

    def test_write_tool_pauses_then_executes_only_after_approval(self):
        agent_module = import_agent(self)
        fake_model = ToolCallingFakeModel(
            responses=[write_request(), AIMessage(content="已写入。")]
        )
        with patch("agent.ChatDeepSeek", return_value=fake_model):
            runtime = agent_module.create_agent_runtime(make_settings())

        config = {"configurable": {"thread_id": "approve-test"}, "recursion_limit": 40}
        with patch("nailong.tools.files.FileSession.write_file", return_value={"ok": True}) as write_file:
            pending = runtime.invoke(
                {"messages": [HumanMessage(content="创建文件")]}, config, version="v2"
            )
            write_file.assert_not_called()
            request = pending.interrupts[0].value["action_requests"][0]
            self.assertEqual(request["name"], "write_file")
            self.assertEqual(request["args"]["path"], "hello.txt")

            finished = runtime.invoke(
                Command(resume={"decisions": [{"type": "approve"}]}),
                config,
                version="v2",
            )

        write_file.assert_called_once()
        self.assertEqual(finished.value["messages"][-1].content, "已写入。")

    def test_rejected_write_is_never_executed(self):
        agent_module = import_agent(self)
        fake_model = ToolCallingFakeModel(
            responses=[write_request(), AIMessage(content="已按拒绝处理。")]
        )
        with patch("agent.ChatDeepSeek", return_value=fake_model):
            runtime = agent_module.create_agent_runtime(make_settings())

        config = {"configurable": {"thread_id": "reject-test"}, "recursion_limit": 40}
        with patch("nailong.tools.files.FileSession.write_file", return_value={"ok": True}) as write_file:
            runtime.invoke(
                {"messages": [HumanMessage(content="创建文件")]}, config, version="v2"
            )
            finished = runtime.invoke(
                Command(resume={"decisions": [{"type": "reject"}]}),
                config,
                version="v2",
            )

        write_file.assert_not_called()
        self.assertEqual(finished.value["messages"][-1].content, "已按拒绝处理。")

    def test_edit_tool_requires_approval_and_executes_only_after_approval(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        target = root / "hello.txt"
        target.write_text("before\n", encoding="utf-8")
        fake_model = ToolCallingFakeModel(
            responses=[file_read_request(), edit_request(), AIMessage(content="已修改。")]
        )
        with (
            patch("agent.ChatDeepSeek", return_value=fake_model),
            patch("local_tools.PROJECT_ROOT", root),
        ):
            runtime = AgentRuntimeFactory(make_settings(root))()

        config = {"configurable": {"thread_id": "edit-approve-test"}, "recursion_limit": 40}
        pending = runtime.invoke(
            {"messages": [HumanMessage(content="修改文件")]}, config, version="v2"
        )
        request = pending.interrupts[0].value["action_requests"][0]
        self.assertEqual(request["name"], "edit_file")
        self.assertEqual(target.read_text(encoding="utf-8"), "before\n")

        finished = runtime.invoke(
            Command(resume={"decisions": [{"type": "approve"}]}),
            config,
            version="v2",
        )

        self.assertEqual(target.read_text(encoding="utf-8"), "after\n")
        self.assertEqual(finished.value["messages"][-1].content, "已修改。")

    def test_rejected_edit_tool_does_not_change_the_file(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        target = root / "hello.txt"
        target.write_text("before\n", encoding="utf-8")
        fake_model = ToolCallingFakeModel(
            responses=[file_read_request(), edit_request(), AIMessage(content="修改已拒绝。")]
        )
        with (
            patch("agent.ChatDeepSeek", return_value=fake_model),
            patch("local_tools.PROJECT_ROOT", root),
        ):
            runtime = AgentRuntimeFactory(make_settings(root))()

        config = {"configurable": {"thread_id": "edit-reject-test"}, "recursion_limit": 40}
        pending = runtime.invoke(
            {"messages": [HumanMessage(content="修改文件")]}, config, version="v2"
        )
        finished = runtime.invoke(
            Command(resume={"decisions": [{"type": "reject"}]}),
            config,
            version="v2",
        )

        self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
        self.assertEqual(finished.value["messages"][-1].content, "修改已拒绝。")

    def test_edit_snapshot_survives_runtime_rebuild_for_the_same_thread(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        target = root / "hello.txt"
        target.write_text("before\n", encoding="utf-8")
        fake_model = ToolCallingFakeModel(
            responses=[
                file_read_request(),
                AIMessage(content="已经读取。"),
                edit_request(),
                AIMessage(content="已经修改。"),
            ]
        )
        with (
            patch("agent.ChatDeepSeek", return_value=fake_model),
            patch("local_tools.PROJECT_ROOT", root),
        ):
            factory = AgentRuntimeFactory(make_settings(root))

        config = {"configurable": {"thread_id": "persistent-edit-test"}, "recursion_limit": 40}
        first_runtime = factory(thread_id="persistent-edit-test")
        first = first_runtime.invoke(
            {"messages": [HumanMessage(content="读取文件")]}, config, version="v2"
        )
        self.assertEqual(first.value["messages"][-1].content, "已经读取。")

        second_runtime = factory(thread_id="persistent-edit-test")
        pending = second_runtime.invoke(
            {"messages": [HumanMessage(content="修改文件")]}, config, version="v2"
        )
        self.assertEqual(pending.interrupts[0].value["action_requests"][0]["name"], "edit_file")
        finished = second_runtime.invoke(
            Command(resume={"decisions": [{"type": "approve"}]}),
            config,
            version="v2",
        )

        self.assertEqual(target.read_text(encoding="utf-8"), "after\n")
        self.assertEqual(finished.value["messages"][-1].content, "已经修改。")

    def test_multiple_approval_decisions_follow_request_order(self):
        agent_module = import_agent(self)
        fake_model = ToolCallingFakeModel(
            responses=[multiple_requests(), AIMessage(content="已处理两项操作。")]
        )
        with patch("agent.ChatDeepSeek", return_value=fake_model):
            runtime = agent_module.create_agent_runtime(make_settings())

        config = {"configurable": {"thread_id": "multiple-test"}, "recursion_limit": 40}
        with (
            patch("local_tools.run_command", return_value={"ok": True}) as run_command,
            patch("nailong.tools.files.FileSession.write_file", return_value={"ok": True}) as write_file,
        ):
            pending = runtime.invoke(
                {"messages": [HumanMessage(content="两项操作")]}, config, version="v2"
            )
            requests = [request for interruption in pending.interrupts
                        for request in interruption.value["action_requests"]]
            self.assertEqual([request["name"] for request in requests], ["run_command", "write_file"])
            run_command.assert_not_called()
            write_file.assert_not_called()

            finished = runtime.invoke(
                Command(
                    resume={interruption.id: {"decisions": [decision]}
                        for interruption, decision in zip(pending.interrupts,
                            [{"type": "approve"}, {"type": "reject"}], strict=True)}
                ),
                config,
                version="v2",
            )

        run_command.assert_called_once()
        write_file.assert_not_called()
        self.assertEqual(finished.value["messages"][-1].content, "已处理两项操作。")


class AgentRuntimeAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_sqlite_runtime_survives_factory_restart_and_rewind(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            data_root = Path(directory) / "agent-data"
            settings = make_settings(root)
            store = ProjectSessionStore(root, base_dir=data_root)
            fake_model = ToolCallingFakeModel(
                responses=[AIMessage(content="第一轮回答"), AIMessage(content="第二轮回答")]
            )
            with patch("agent.ChatDeepSeek", return_value=fake_model):
                factory = AgentRuntimeFactory(settings, session_store=store)
            service = AgentService(factory, api_key=settings.api_key)
            config = {"configurable": {"thread_id": "async-persist"}, "recursion_limit": 40}

            self.assertEqual(await service.run_turn("第一轮问题", config), "第一轮回答")
            self.assertEqual(await service.run_turn("第二轮问题", config), "第二轮回答")
            removed = await service.rewind("async-persist")
            runtime = await factory.async_runtime(thread_id="async-persist")
            state = await runtime.aget_state(config)
            self.assertEqual(removed, 2)
            self.assertEqual(
                [message.content for message in state.values["messages"]],
                ["第一轮问题", "第一轮回答"],
            )
            await factory.aclose()
            factory.close()

            restored_model = ToolCallingFakeModel(responses=[AIMessage(content="恢复后继续")])
            with patch("agent.ChatDeepSeek", return_value=restored_model):
                reopened = AgentRuntimeFactory(
                    settings,
                    session_store=ProjectSessionStore(root, base_dir=data_root),
                )
            restored_runtime = await reopened.async_runtime(thread_id="async-persist")
            restored = await restored_runtime.aget_state(config)
            self.assertEqual(
                [message.content for message in restored.values["messages"]],
                ["第一轮问题", "第一轮回答"],
            )
            await reopened.aclose()
            reopened.close()

    async def test_async_service_uses_real_langgraph_interrupt_and_resume(self):
        fake_model = ToolCallingFakeModel(
            responses=[write_request(), AIMessage(content="异步审批完成。")]
        )
        with patch("agent.ChatDeepSeek", return_value=fake_model):
            factory = AgentRuntimeFactory(make_settings())
        service = AgentService(factory, api_key="unit-test-key")
        decisions = []
        statuses = []

        async def approve(action, index, total):
            decisions.append((action["name"], index, total))
            return "approve"

        config = {
            "configurable": {"thread_id": "async-runtime-test"},
            "recursion_limit": 40,
        }
        with patch("nailong.tools.files.FileSession.write_file", return_value={"ok": True}) as write_file:
            answer = await service.run_turn(
                "创建 hello.txt",
                config,
                approval_handler=approve,
                status_handler=statuses.append,
            )

        self.assertEqual(answer, "异步审批完成。")
        self.assertEqual(decisions, [("write_file", 1, 1)])
        self.assertEqual(statuses, ["thinking", "waiting_approval", "thinking"])
        self.assertEqual(write_file.call_count, 1)
        self.assertEqual(write_file.call_args.args, ('hello.txt', 'hello'))
        self.assertIn('expected_version', write_file.call_args.kwargs)

    async def test_init_requires_approval_for_creation_and_overwrite(self):
        fake_model = ToolCallingFakeModel(
            responses=[
                context_write_request("首次生成"),
                AIMessage(content="已创建上下文。"),
                AIMessage(content='', tool_calls=[{'name': 'read_file', 'args': {'path': '.nailong/context.md'}, 'id': 'context-read-before-replace'}]),
                context_write_request("更新上下文"),
                AIMessage(content="已更新上下文。"),
            ]
        )
        with patch("agent.ChatDeepSeek", return_value=fake_model):
            approvals = []
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                settings = Settings(
                    api_key="unit-test-key",
                    api_base="https://api.deepseek.com",
                    model="deepseek-chat",
                    project_root=root,
                )
                factory = AgentRuntimeFactory(settings)
                service = AgentService(factory, api_key=settings.api_key)
                config = {
                    "configurable": {"thread_id": "init-create-overwrite"},
                    "recursion_limit": 40,
                }

                async def approve(action, index, total):
                    approvals.append((action["args"]["content"], index, total))
                    return "approve"

                with patch("local_tools.PROJECT_ROOT", root):
                    with patch(
                        "nailong.tools.files.FileSession.write_file",
                        autospec=True,
                        side_effect=__import__('nailong.tools.files', fromlist=['FileSession']).FileSession.write_file,
                    ) as write_file:
                        first = await service.run_turn(
                            "/init",
                            config,
                            profile="init",
                            approval_handler=approve,
                        )
                        second = await service.run_turn(
                            "/init",
                            config,
                            profile="init",
                            approval_handler=approve,
                        )

                context_file = root / ".nailong" / "context.md"
                self.assertEqual(first, "已创建上下文。")
                self.assertEqual(second, "已更新上下文。")
                self.assertEqual(context_file.read_text(encoding="utf-8"), "更新上下文")
                self.assertEqual(
                    approvals,
                    [("首次生成", 1, 1), ("更新上下文", 1, 1)],
                )
                self.assertEqual(write_file.call_count, 2)


if __name__ == "__main__":
    unittest.main()
