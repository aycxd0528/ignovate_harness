import importlib
import asyncio
import io
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from config import Settings
import local_tools


_TEST_DATA_DIRECTORY = None
_ORIGINAL_DATA_DIRECTORY = None


def setUpModule():
    global _TEST_DATA_DIRECTORY, _ORIGINAL_DATA_DIRECTORY
    _ORIGINAL_DATA_DIRECTORY = os.environ.get("NAILONG_DATA_DIR")
    _TEST_DATA_DIRECTORY = tempfile.TemporaryDirectory(prefix="nailong-cli-tests-")
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


def import_main(test_case):
    try:
        return importlib.import_module("main")
    except ModuleNotFoundError as error:
        test_case.fail(f"main module is missing: {error}")


class CliTests(unittest.TestCase):
    def test_skip_permissions_flag_reaches_all_interactive_interfaces(self):
        main = import_main(self)
        settings = Settings('unit-test-key', 'https://api.deepseek.com', 'deepseek-chat', Path(tempfile.gettempdir()))
        for flag, target in (('--plain', 'run_cli'), ('--tui', 'run_tui'), ('inline', 'run_inline')):
            with self.subTest(interface=target), patch('main.load_settings', return_value=settings):
                args = ['--ui', 'inline'] if flag == 'inline' else [flag]
                with patch.object(main, target) as interface:
                    self.assertEqual(main.main([*args, '--dangerously-skip-permissions']), 0)
                if target == 'run_inline':
                    interface.assert_awaited_once_with(settings, permission_mode='bypassPermissions')
                else:
                    interface.assert_called_once_with(settings, permission_mode='bypassPermissions')

    def test_skip_permissions_flag_reaches_headless_without_starting_ui(self):
        main = import_main(self)
        settings = Settings('unit-test-key', 'https://api.deepseek.com', 'deepseek-chat', Path(tempfile.gettempdir()))
        with patch('main.load_settings', return_value=settings), \
                patch('headless.run_print', new_callable=AsyncMock, return_value=0) as run_print, \
                patch('main.run_cli') as cli, patch('main.run_tui') as tui, patch('main.run_inline') as inline:
            self.assertEqual(main.main(['-p', '检查项目', '--dangerously-skip-permissions']), 0)
        run_print.assert_awaited_once_with(settings, '检查项目', output_format='text', max_turns=40,
            permission_mode='bypassPermissions')
        cli.assert_not_called()
        tui.assert_not_called()
        inline.assert_not_called()

    def test_skip_permissions_conflicts_with_explicit_permission_modes(self):
        main = import_main(self)
        for mode in ('default', 'acceptEdits', 'plan'):
            with self.subTest(mode=mode), patch('sys.stderr', new_callable=io.StringIO) as error, \
                    patch('main.load_settings') as load:
                with self.assertRaises(SystemExit) as stopped:
                    main.main(['--permission-mode', mode, '--dangerously-skip-permissions'])
                self.assertEqual(stopped.exception.code, 2)
                self.assertIn('--permission-mode', error.getvalue())
                self.assertIn('--dangerously-skip-permissions', error.getvalue())
                load.assert_not_called()

    def test_print_still_rejects_other_permission_modes(self):
        main = import_main(self)
        for mode in ('acceptEdits', 'plan'):
            with self.subTest(mode=mode), patch('sys.stderr', new_callable=io.StringIO), \
                    patch('main.load_settings') as load:
                with self.assertRaises(SystemExit) as stopped:
                    main.main(['-p', '检查项目', '--permission-mode', mode])
                self.assertEqual(stopped.exception.code, 2)
                load.assert_not_called()

    def test_permission_mode_flag_is_forwarded_and_session_choice_is_supported(self):
        main = import_main(self)
        settings = Settings("unit-test-key", "https://api.deepseek.com", "deepseek-chat", Path(tempfile.gettempdir()))
        with (
            patch("main.load_settings", return_value=settings),
            patch("main.run_cli") as run_cli,
        ):
            main.main(["--plain", "--permission-mode", "acceptEdits"])

        run_cli.assert_called_once_with(settings, permission_mode="acceptEdits")
        self.assertEqual(main._ask_decision(lambda _prompt: "s"), "approve_session")
        self.assertEqual(main._ask_decision(lambda _prompt: ""), "reject")

    def test_session_flags_are_forwarded_to_both_interactive_modes(self):
        main = import_main(self)
        settings = Settings("unit-test-key", "https://api.deepseek.com", "deepseek-chat", Path(tempfile.gettempdir()))
        with (
            patch("main.load_settings", return_value=settings),
            patch("main.run_cli") as run_cli,
            patch("main.run_inline") as run_inline,
        ):
            main.main(["--plain", "--continue"])
            main.main(["--plain", "--resume", "saved-session"])
            main.main(["--ui", "inline", "--continue"])

        self.assertEqual(
            run_cli.call_args_list,
            [
                unittest.mock.call(settings, continue_session=True),
                unittest.mock.call(settings, resume_session="saved-session"),
            ],
        )
        run_inline.assert_awaited_once_with(settings, continue_session=True)

    def test_print_mode_routes_to_headless_runner_without_starting_a_ui(self):
        main = import_main(self)
        settings = Settings("unit-test-key", "https://api.deepseek.com", "deepseek-chat", Path(tempfile.gettempdir()))
        with (
            patch("main.load_settings", return_value=settings),
            patch("headless.run_print", new_callable=AsyncMock, return_value=0) as run_print,
            patch("main.run_cli") as run_cli,
            patch("main.run_tui") as run_tui,
            patch("main.run_inline") as run_inline,
        ):
            code = main.main(["-p", "检查项目", "--output-format", "json", "--max-turns", "12"])

        self.assertEqual(code, 0)
        run_print.assert_awaited_once_with(
            settings,
            "检查项目",
            output_format="json",
            max_turns=12,
        )
        run_cli.assert_not_called()
        run_tui.assert_not_called()
        run_inline.assert_not_awaited()

    def test_headless_goal_mode_forwards_user_budgets_without_starting_ui(self):
        main = import_main(self)
        settings = Settings("unit-test-key", "https://api.deepseek.com", "deepseek-chat", Path(tempfile.gettempdir()))
        with (
            patch("main.load_settings", return_value=settings),
            patch("headless.run_print", new_callable=AsyncMock, return_value=1) as run_print,
            patch("main.run_cli") as run_cli,
            patch("main.run_tui") as run_tui,
            patch("main.run_inline") as run_inline,
        ):
            code = main.main([
                "-p", "修复目标", "--goal", "--output-format", "json",
                "--goal-max-rounds", "5", "--goal-max-cost-usd", "0.25",
            ])

        self.assertEqual(code, 1)
        run_print.assert_awaited_once_with(
            settings,
            "修复目标",
            output_format="json",
            max_turns=40,
            goal_mode=True,
            goal_max_rounds=5,
            goal_max_cost_usd=0.25,
        )
        run_cli.assert_not_called()
        run_tui.assert_not_called()
        run_inline.assert_not_awaited()


    def test_plain_repl_reuses_one_event_loop_for_multiple_model_turns(self):
        main = import_main(self)
        settings = Settings("unit-test-key", "https://api.deepseek.com", "deepseek-chat", Path(tempfile.gettempdir()))

        class LoopBoundGraph:
            def __init__(self):
                self.loop = None
                self.calls = 0

            async def ainvoke(self, *_args, **_kwargs):
                running_loop = asyncio.get_running_loop()
                if self.loop is not None and self.loop is not running_loop:
                    raise RuntimeError("Event loop is closed")
                self.loop = running_loop
                self.calls += 1
                return SimpleNamespace(
                    value={"messages": [AIMessage(content=f"回答 {self.calls}")]},
                    interrupts=(),
                )

        graph = LoopBoundGraph()
        displayed = []
        answers = iter(["第一轮", "第二轮", "/exit"])
        with patch("main.create_agent_runtime", return_value=graph):
            main.run_cli(settings, input_fn=lambda _prompt: next(answers), output_fn=displayed.append)

        self.assertEqual(graph.calls, 2)
        self.assertIn("回答 1", displayed)
        self.assertIn("回答 2", displayed)
        self.assertNotIn("Event loop is closed", "\n".join(displayed))

    def test_plain_repl_confirms_rewind_before_changing_dialog_state(self):
        main = import_main(self)
        settings = Settings("unit-test-key", "https://api.deepseek.com", "deepseek-chat", Path(tempfile.gettempdir()))

        class Service:
            session_store = None

            def __init__(self, *_args, **_kwargs):
                self.rewinds = 0

            async def rewind(self, _thread_id):
                self.rewinds += 1
                return 2

        service = Service()
        answers = iter(["/rewind", "n", "/rewind", "y", "/exit"])
        prompts = []
        displayed = []
        with (
            patch("main.create_agent_runtime", return_value=object()),
            patch("main.AgentService", return_value=service),
        ):
            main.run_cli(
                settings,
                input_fn=lambda prompt: (prompts.append(prompt), next(answers))[1],
                output_fn=displayed.append,
            )

        self.assertEqual(service.rewinds, 1)
        self.assertEqual(sum("确认回退" in prompt for prompt in prompts), 2)
        self.assertIn("已取消回退", "\n".join(displayed))
        self.assertIn("不会撤销", "\n".join(displayed))

    def test_project_argument_selects_external_root_for_both_interfaces(self):
        main = import_main(self)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "图书管理系统"
            root.mkdir()
            (root / "main.c").write_text("int main(void) { return 0; }", encoding="utf-8")
            settings = Settings("unit-test-key", "https://api.deepseek.com", "deepseek-chat", Path(tempfile.gettempdir()))
            original_root = local_tools.PROJECT_ROOT
            observed = []

            def check_runtime_root(selected_settings):
                observed.append(selected_settings.project_root)
                from tools import build_tools

                listing = next(tool for tool in build_tools() if tool.name == "list_files")
                reading = next(tool for tool in build_tools() if tool.name == "read_file")
                self.assertIn("main.c", json.loads(listing.invoke({"path": "."}))["files"])
                self.assertTrue(json.loads(reading.invoke({"path": str(root / "main.c")}))["ok"])
                self.assertFalse(json.loads(reading.invoke({"path": str(root.parent / "other.c")}))["ok"])

            with (
                patch("main.load_settings", return_value=settings),
                patch("main.run_cli", side_effect=check_runtime_root) as run_cli,
                patch("main.run_tui", side_effect=check_runtime_root) as run_tui,
                patch('builtins.print') as printed,
            ):
                main.main(["--plain", "--project", str(root)])
                main.main(["--tui", "--project", str(root)])

            self.assertEqual(observed, [root.resolve(), root.resolve()])
            self.assertEqual(local_tools.PROJECT_ROOT, original_root)
            run_cli.assert_called_once()
            run_tui.assert_called_once()
            self.assertFalse(any('启动失败' in str(call) for call in printed.call_args_list))

    def test_invalid_project_argument_does_not_start_agent(self):
        main = import_main(self)
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            settings = Settings("unit-test-key", "https://api.deepseek.com", "deepseek-chat", Path(tempfile.gettempdir()))
            printed = []
            with (
                patch("main.load_settings", return_value=settings),
                patch("main.run_cli") as run_cli,
                patch("main.run_tui") as run_tui,
                patch("builtins.print", side_effect=printed.append),
            ):
                main.main(["--plain", "--project", str(missing)])
            run_cli.assert_not_called()
            run_tui.assert_not_called()
            self.assertIn("项目目录", "\n".join(printed))

    def test_plain_project_command_switches_root_and_starts_fresh_history(self):
        main = import_main(self)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "图书管理系统"
            root.mkdir()
            settings = Settings("unit-test-key", "https://api.deepseek.com", "deepseek-chat", Path(tempfile.gettempdir()))
            original_root = local_tools.PROJECT_ROOT
            created_agents = []
            observed = []

            def create_agent(_settings):
                agent = object()
                created_agents.append(agent)
                return agent

            def fake_run_turn(agent, message, config, **_kwargs):
                observed.append((agent, message, config["configurable"]["thread_id"], local_tools.selected_project_root()))
                return "已完成"

            answers = iter(["原项目问题", f"/project {root}", "新项目问题", "/exit"])
            displayed = []
            with (
                patch("main.create_agent_runtime", side_effect=create_agent),
                patch("main.run_turn", side_effect=fake_run_turn),
            ):
                main.run_cli(settings, input_fn=lambda _prompt: next(answers), output_fn=displayed.append)

            self.assertEqual(len(created_agents), 2)
            self.assertIs(observed[0][0], created_agents[0])
            self.assertIs(observed[1][0], created_agents[1])
            self.assertNotEqual(observed[0][2], observed[1][2])
            self.assertEqual(observed[1][3], root.resolve())
            self.assertIn(str(root), "\n".join(displayed))
            self.assertEqual(local_tools.PROJECT_ROOT, original_root)

    def test_run_turn_prompts_for_each_tool_and_resumes_in_order(self):
        main = import_main(self)
        from agent import create_agent_runtime

        fake_model = ToolCallingFakeModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "run_command",
                            "args": {"command": "pwd"},
                            "id": "cmd-1",
                        },
                        {
                            "name": "write_file",
                            "args": {"path": "hello.txt", "content": "hello"},
                            "id": "write-1",
                        },
                    ],
                ),
                AIMessage(content="已经按你的决定处理。"),
            ]
        )
        settings = Settings(
            api_key="unit-test-key",
            api_base="https://api.deepseek.com",
            model="deepseek-chat",
            project_root=Path(tempfile.mkdtemp(prefix="project-", dir=_TEST_DATA_DIRECTORY.name)),
        )
        with patch("agent.ChatDeepSeek", return_value=fake_model):
            runtime = create_agent_runtime(settings)

        decisions = iter(["a", "r"])
        prompts = []
        displayed = []
        def next_decision(prompt):
            prompts.append(prompt)
            try:
                return next(decisions)
            except StopIteration as error:
                state = runtime.get_state(config)
                messages = [
                    (item.type, getattr(item, "id", None), getattr(item, "tool_calls", None),
                     getattr(item, "tool_call_id", None), str(item.content)[:100])
                    for item in state.values.get("messages", [])
                ]
                raise AssertionError(
                    f"Unexpected third approval; model_i={fake_model.i!r}; "
                    f"messages={messages!r}; displayed={displayed!r}"
                ) from error

        config = {"configurable": {"thread_id": "cli-test"}, "recursion_limit": 40}
        with (
            patch("nailong.core.processes.execute_process", return_value={"ok": True}) as run_command,
            patch("nailong.tools.files.FileSession.write_file", return_value={"ok": True}) as write_file,
        ):
            answer = main.run_turn(
                runtime,
                "做两件事",
                config,
                input_fn=next_decision,
                output_fn=displayed.append,
                api_key=settings.api_key,
            )

        self.assertEqual(answer, "已经按你的决定处理。")
        self.assertEqual(len(prompts), 2)
        self.assertTrue(any("pwd" in line for line in displayed))
        self.assertTrue(any("工作目录" in line for line in displayed))
        run_command.assert_called_once()
        write_file.assert_not_called()

    def test_run_cli_keeps_history_in_memory_and_supports_clear(self):
        main = import_main(self)
        settings = Settings(
            api_key="secret-sentinel",
            api_base="https://api.deepseek.com",
            model="deepseek-chat",
            project_root=Path(tempfile.gettempdir()),
        )
        observed_configs = []

        class FakeGraph:
            def get_state(self, config):
                observed_configs.append(config)
                return SimpleNamespace(
                    values={
                        "messages": [
                            SimpleNamespace(type="human", content="你好 secret-sentinel"),
                            SimpleNamespace(type="ai", content="你好"),
                        ]
                    }
                )

        answers = iter(["/history", "/count", "/clear", "/history", "/exit"])
        displayed = []
        with (
            patch("main.load_settings", return_value=settings),
            patch("main.create_agent_runtime", return_value=FakeGraph()),
        ):
            main.run_cli(
                input_fn=lambda prompt: next(answers),
                output_fn=displayed.append,
            )

        joined = "\n".join(displayed)
        self.assertIn("[密钥已隐藏]", joined)
        self.assertNotIn("secret-sentinel", joined)
        self.assertIn("本次运行已完成 0 轮对话", joined)
        self.assertEqual(len(observed_configs), 2)
        self.assertNotEqual(
            observed_configs[0]["configurable"]["thread_id"],
            observed_configs[1]["configurable"]["thread_id"],
        )

    def test_tool_arguments_containing_the_model_key_are_hidden_and_rejected(self):
        main = import_main(self)
        from agent import create_agent_runtime

        secret = "secret-sentinel"
        fake_model = ToolCallingFakeModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"path": "copy.txt", "content": secret},
                            "id": "secret-write",
                        }
                    ],
                ),
                AIMessage(content="该请求已拒绝。"),
            ]
        )
        settings = Settings(
            api_key=secret,
            api_base="https://api.deepseek.com",
            model="deepseek-chat",
            project_root=Path(tempfile.gettempdir()),
        )
        with patch("agent.ChatDeepSeek", return_value=fake_model):
            runtime = create_agent_runtime(settings)

        displayed = []
        prompts = []
        config = {"configurable": {"thread_id": "secret-approval"}, "recursion_limit": 40}
        with patch("local_tools.write_file", return_value={"ok": True}) as write_file:
            answer = main.run_turn(
                runtime,
                "创建文件",
                config,
                input_fn=lambda prompt: (prompts.append(prompt), "a")[1],
                output_fn=displayed.append,
                api_key=secret,
            )

        self.assertEqual(answer, "该请求已拒绝。")
        self.assertEqual(prompts, [])
        self.assertNotIn(secret, "\n".join(displayed))
        write_file.assert_not_called()

    def test_friendly_error_redacts_before_truncating(self):
        main = import_main(self)
        secret = "secret-sentinel"
        error = RuntimeError("x" * 495 + secret + " trailing detail")

        message = main._friendly_error(error, secret)

        self.assertNotIn(secret[:5], message)
        self.assertIn("[密钥已隐藏]", message)

    def test_empty_latest_answer_does_not_reuse_an_earlier_turn(self):
        main = import_main(self)

        class GraphWithEmptyFinalAnswer:
            async def ainvoke(self, *args, **kwargs):
                return SimpleNamespace(
                    value={
                        "messages": [
                            SimpleNamespace(type="ai", content="上一轮回答"),
                            SimpleNamespace(type="human", content="新问题"),
                            SimpleNamespace(type="ai", content=""),
                        ]
                    },
                    interrupts=(),
                )

        answer = main.run_turn(
            GraphWithEmptyFinalAnswer(),
            "新问题",
            {"configurable": {"thread_id": "empty-answer"}, "recursion_limit": 40},
            input_fn=lambda prompt: "",
            output_fn=lambda text: None,
        )

        self.assertEqual(answer, "模型返回了空回答。")

    def test_main_auto_uses_textual_when_supported_and_honors_explicit_modes(self):
        main = import_main(self)
        settings = Settings(
            api_key="unit-test-key",
            api_base="https://api.deepseek.com",
            model="deepseek-chat",
            project_root=Path(tempfile.gettempdir()),
        )

        with (
            patch("main.load_settings", return_value=settings),
            patch("main._supports_fullscreen_tui", return_value=True),
            patch("main._supports_inline_ui", return_value=True),
            patch("main.run_tui") as run_tui,
            patch("main.run_cli") as run_cli,
            patch("main.run_inline") as run_inline,
        ):
            main.main([])
            main.main(["--ui", "auto"])
            main.main(["--ui", "inline"])
            main.main(["--plain"])
            main.main(["--tui"])
            main.main(["--ui", "textual"])

        run_inline.assert_called_once_with(settings)
        run_cli.assert_called_once_with(settings)
        self.assertEqual(run_tui.call_count, 4)
        self.assertTrue(all(call.args == (settings,) for call in run_tui.call_args_list))

    def test_main_auto_uses_inline_on_tty_without_fullscreen(self):
        main = import_main(self)
        settings = Settings("key", "https://api.deepseek.com", "deepseek-chat", Path(tempfile.gettempdir()))
        with (
            patch("main.load_settings", return_value=settings),
            patch("main._supports_fullscreen_tui", return_value=False),
            patch("main._supports_inline_ui", return_value=True),
            patch("main.run_tui") as run_tui,
            patch("main.run_cli") as run_cli,
            patch("main.run_inline") as run_inline,
        ):
            main.main([])
        run_inline.assert_called_once_with(settings)
        run_tui.assert_not_called()
        run_cli.assert_not_called()

    def test_default_mode_falls_back_to_plain_in_pycharm(self):
        main = import_main(self)
        settings = Settings(
            api_key="unit-test-key",
            api_base="https://api.deepseek.com",
            model="deepseek-chat",
            project_root=Path(tempfile.gettempdir()),
        )
        with (
            patch.dict(os.environ, {"PYCHARM_HOSTED": "1"}),
            patch.object(main.sys, "stdin", SimpleNamespace(isatty=lambda: True)),
            patch.object(main.sys, "stdout", SimpleNamespace(isatty=lambda: True)),
            patch("main.load_settings", return_value=settings),
            patch("main.run_tui") as run_tui,
            patch("main.run_cli") as run_cli,
            patch("main.run_inline") as run_inline,
        ):
            main.main([])

        run_tui.assert_not_called()
        run_cli.assert_called_once_with(settings)
        run_inline.assert_not_called()

    def test_pycharm_tty_and_unknown_mac_tty_do_not_start_fullscreen(self):
        main = import_main(self)
        fake_tty = SimpleNamespace(isatty=lambda: True)

        with (
            patch.object(main.sys, "stdin", fake_tty),
            patch.object(main.sys, "stdout", fake_tty),
            patch.object(main.sys, "platform", "darwin"),
            patch.dict(
                os.environ,
                {
                    "PYCHARM_HOSTED": "1",
                    "TERM": "xterm-256color",
                    "TERM_PROGRAM": "Apple_Terminal",
                },
                clear=True,
            ),
        ):
            self.assertFalse(main._supports_fullscreen_tui())

        with (
            patch.object(main.sys, "stdin", fake_tty),
            patch.object(main.sys, "stdout", fake_tty),
            patch.object(main.sys, "platform", "darwin"),
            patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True),
        ):
            self.assertFalse(main._supports_fullscreen_tui())

        with (
            patch.object(main.sys, "stdin", fake_tty),
            patch.object(main.sys, "stdout", fake_tty),
            patch.object(main.sys, "platform", "darwin"),
            patch.dict(
                os.environ,
                {"TERM": "xterm-256color", "TERMINAL_EMULATOR": "JetBrains-JediTerm"},
                clear=True,
            ),
        ):
            self.assertFalse(main._supports_fullscreen_tui())

        with (
            patch.object(main.sys, "stdin", fake_tty),
            patch.object(main.sys, "stdout", fake_tty),
            patch.object(main.sys, "platform", "darwin"),
            patch.dict(
                os.environ,
                {"TERM": "xterm-256color", "TERM_PROGRAM": "Apple_Terminal"},
                clear=True,
            ),
        ):
            self.assertTrue(main._supports_fullscreen_tui())

    def test_tui_startup_errors_redact_already_loaded_api_key(self):
        main = import_main(self)
        secret = "startup-secret-sentinel"
        settings = Settings(
            api_key=secret,
            api_base="https://api.deepseek.com",
            model="deepseek-chat",
            project_root=Path(tempfile.gettempdir()),
        )
        printed = []

        with (
            patch("main.load_settings", return_value=settings),
            patch("main.run_tui", side_effect=RuntimeError(f"failed with {secret}")),
            patch("builtins.print", side_effect=printed.append),
        ):
            main.main(["--tui"])

        self.assertNotIn(secret, "\n".join(printed))
        self.assertIn("[密钥已隐藏]", "\n".join(printed))

    def test_model_call_budget_is_shared_across_approval_resumes(self):
        main = import_main(self)
        from agent import create_agent_runtime

        requests = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": f"file-{index}.txt", "content": "x"},
                        "id": f"write-{index}",
                    }
                ],
            )
            for index in range(45)
        ]
        fake_model = ToolCallingFakeModel(responses=requests + [AIMessage(content="完成")])
        with tempfile.TemporaryDirectory() as directory, asyncio.Runner() as runner:
            root = Path(directory)
            settings = Settings("unit-test-key", "https://api.deepseek.com", "deepseek-chat", root)
            with patch("agent.ChatDeepSeek", return_value=fake_model):
                runtime = create_agent_runtime(settings)
            factory = runtime._nailong_runtime_factory
            prompts = []
            thread_id = f"budget-{uuid.uuid4().hex}"
            config = {"configurable": {"thread_id": thread_id}}
            try:
                with self.assertRaises(main.TurnRecursionLimitError) as stopped:
                    main.run_turn(
                        runtime, "请反复写文件", config,
                        input_fn=lambda prompt: (prompts.append(prompt), "a")[1],
                        output_fn=lambda text: None, api_key=settings.api_key, runner=runner,
                    )

                # Every resume uses the same model budget; call 40 must never
                # reach approval or execute its requested file write.
                self.assertEqual(stopped.exception.stats["limit_type"], "model")
                self.assertEqual(stopped.exception.stats["graph_step_limit"], 256)
                self.assertEqual(stopped.exception.stats["model_call_limit"], 40)
                self.assertLessEqual(fake_model.i, 40)
                self.assertEqual(fake_model.i, 40)
                self.assertEqual(stopped.exception.stats["model_calls"], 40)
                self.assertLessEqual(len(prompts), 39)
                self.assertEqual(len(prompts), 39)
                self.assertEqual(stopped.exception.stats["tool_calls"], 39)
                self.assertEqual(sorted(path.name for path in root.glob('file-*.txt')),
                    sorted(f'file-{index}.txt' for index in range(39)))
                self.assertTrue(all((root / f'file-{index}.txt').read_text() == 'x' for index in range(39)))
                self.assertFalse((root / 'file-39.txt').exists())
                calls = [event['data'] for event in factory.session_store.read_events(thread_id)
                    if event['kind'] == 'model_call']
                self.assertEqual([call['model_call'] for call in calls], list(range(1, 41)))
                self.assertTrue(calls[-1]['summary_only'])
            finally:
                runner.run(factory.aclose())
                factory.close()

    def test_last_model_call_can_answer_after_39_approval_resumes(self):
        main = import_main(self)
        from agent import create_agent_runtime

        requests = [AIMessage(content='', tool_calls=[{
            'name': 'write_file', 'args': {'path': f'file-{index}.txt', 'content': 'x'},
            'id': f'write-{index}',
        }]) for index in range(39)]
        answer = '已完成写入；尚未验证项目。'
        fake_model = ToolCallingFakeModel(responses=[*requests, AIMessage(content=answer),
            AIMessage(content='不应发起第 41 次请求')])
        with tempfile.TemporaryDirectory() as directory, asyncio.Runner() as runner:
            root = Path(directory)
            settings = Settings("unit-test-key", "https://api.deepseek.com", "deepseek-chat", root)
            with patch("agent.ChatDeepSeek", return_value=fake_model):
                runtime = create_agent_runtime(settings)
            factory = runtime._nailong_runtime_factory
            prompts = []
            thread_id = f'last-answer-{uuid.uuid4().hex}'
            try:
                result = main.run_turn(
                    runtime, '依次写文件后汇报', {'configurable': {'thread_id': thread_id}},
                    input_fn=lambda prompt: (prompts.append(prompt), 'a')[1],
                    output_fn=lambda text: None, api_key=settings.api_key, runner=runner,
                )
                self.assertEqual(result, answer)
                self.assertEqual(fake_model.i, 40)
                self.assertEqual(len(prompts), 39)
                self.assertEqual(len(list(root.glob('file-*.txt'))), 39)
                events = factory.session_store.read_events(thread_id)
                calls = [event['data'] for event in events if event['kind'] == 'model_call']
                self.assertEqual([call['model_call'] for call in calls], list(range(1, 41)))
                self.assertTrue(calls[-1]['summary_only'])
                self.assertFalse(any(event['kind'] == 'turn_limit' for event in events))
                final = next(event['data'] for event in reversed(events) if event['kind'] == 'final')
                self.assertEqual(final['stats']['model_calls'], 40)
                self.assertEqual(sum(event['kind'] == 'tool_start' for event in events), 39)
                self.assertEqual(final['stats']['graph_step_limit'], 256)
                self.assertGreater(final['stats']['graph_steps'], 40)
                self.assertLessEqual(final['stats']['graph_steps'], 256)
            finally:
                runner.run(factory.aclose())
                factory.close()

    def test_run_cli_preserves_task_thread_after_turn_limit(self):
        main = import_main(self)
        settings = Settings(
            api_key="unit-test-key",
            api_base="https://api.deepseek.com",
            model="deepseek-chat",
            project_root=Path(tempfile.gettempdir()),
        )
        calls = []

        def run_turn(agent, message, config, **kwargs):
            calls.append(config)
            if len(calls) == 1:
                raise main.TurnRecursionLimitError("step budget reached")
            return "下一轮回答"

        answers = iter(["重复任务", "新问题", "/exit"])
        displayed = []
        with (
            patch("main.load_settings", return_value=settings),
            patch("main.create_agent_runtime", return_value=object()),
            patch("main.run_turn", side_effect=run_turn),
        ):
            main.run_cli(
                input_fn=lambda prompt: next(answers), output_fn=displayed.append
            )

        self.assertEqual(
            calls[0]["configurable"]["thread_id"],
            calls[1]["configurable"]["thread_id"],
        )
        self.assertIn("下一轮回答", displayed)


if __name__ == "__main__":
    unittest.main()
