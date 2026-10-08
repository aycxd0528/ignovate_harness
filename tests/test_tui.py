import unittest
import tempfile
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from textual.errors import NoWidget
from textual.widgets import RichLog, Static, TextArea

from config import Settings
from agent_service import TurnEvent
from nailong.core.goal import GoalStore
from tui import PlanEditScreen, PlanReviewScreen, RewindConfirmationScreen, TerminalAgentApp, run_tui
import local_tools


class FakeService:
    def __init__(self, request_approval=False, failures=0):
        self.calls = []
        self.request_approval = request_approval
        self.failures = failures
        self.decision = None

    async def run_turn(
        self,
        message,
        config,
        *,
        profile="chat",
        target_path=None,
        approval_handler=None,
        status_handler=None,
        allowed_tools=None,
        history_display=None,
    ):
        self.calls.append(
            {
                "message": message,
                "config": config,
                "profile": profile,
                "target_path": target_path,
            }
        )
        if self.failures:
            self.failures -= 1
            raise RuntimeError("temporary request failure")
        if status_handler:
            status_handler("thinking")
        if self.request_approval and approval_handler:
            self.decision = await approval_handler(
                {
                    "name": "write_file",
                    "args": {"path": ".nailong/context.md", "content": "draft"},
                },
                1,
                1,
            )
        return "Agent 回答"

    def get_history(self, thread_id):
        return [("user", "先前问题"), ("assistant", "先前回答")]


class FakeStreamingService(FakeService):
    def __init__(self):
        super().__init__()
        self.waiting_for_release = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_turn(
        self,
        message,
        config,
        *,
        profile="chat",
        target_path=None,
        approval_handler=None,
        status_handler=None,
        allowed_tools=None,
        history_display=None,
    ):
        self.calls.append({"message": message, "config": config, "profile": profile, "target_path": target_path})
        yield TurnEvent("token", {"text": "Agent "})
        self.waiting_for_release.set()
        await self.release.wait()
        yield TurnEvent("token", {"text": "回答"})
        yield TurnEvent("final", {"text": "Agent 回答"})


def make_settings():
    return Settings(
        api_key="test-key",
        api_base="https://api.deepseek.com",
        model="deepseek-chat",
        project_root=Path.cwd(),
    )


class TerminalAgentAppTests(unittest.IsolatedAsyncioTestCase):
    def interaction_state(self, pilot):
        app = pilot.app
        return (f'focus={getattr(app.focused, "id", None)!r}, '
                f'panel={app._interaction_panel!r}, '
                f'interaction_future={app._interaction_future!r}, '
                f'approval_future={app._approval_future!r}')

    async def wait_for_target(self, pilot, selector, *, focus_id):
        async def ready():
            while True:
                await pilot.pause()
                matches = pilot.app.query(selector)
                if not matches:
                    continue
                target = matches[0]
                if not target.is_attached or not target.region.width or not target.region.height:
                    continue
                if getattr(pilot.app.focused, 'id', None) != focus_id:
                    continue
                try:
                    hit, _ = pilot.app.get_widget_at(*target.region.offset)
                except NoWidget:
                    continue
                if hit is target:
                    return target
        try:
            return await asyncio.wait_for(ready(), 3)
        except TimeoutError:
            self.fail(f'Timed out waiting for visible {selector}; {self.interaction_state(pilot)}')

    async def wait_for_result(self, pilot, awaitable):
        pending = asyncio.create_task(awaitable)
        try:
            return await asyncio.wait_for(asyncio.shield(pending), 3)
        except TimeoutError:
            self.fail(f'Timed out waiting for interaction result; {self.interaction_state(pilot)}')
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    async def test_saved_session_opens_history_and_restored_metrics(self):
        service = FakeService()
        service.session_store = SimpleNamespace(read_events=lambda _thread_id: [
            {"kind": "turn_start"},
            {"kind": "usage", "data": {"input_tokens": 200, "output_tokens": 50, "cache_hit_tokens": 40}},
        ])
        app = TerminalAgentApp(service, make_settings(), thread_id="saved-thread")
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            lines = "\n".join(str(line) for line in app.query_one("#transcript", RichLog).lines)
            self.assertIn("先前问题", lines)
            self.assertIn("先前回答", lines)
            self.assertNotIn("准备就绪", lines)
            stats = app._status_details()
            self.assertIn("会话 1 轮", stats)
            self.assertIn("Token 250", stats)
            self.assertIn("200 / 65.5k", app.query_one('#token-weather', Static).content.plain)

    async def test_plan_review_and_edit_screens_require_explicit_choices(self):
        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="plan-dialogs")
        async with app.run_test(size=(100, 32)) as pilot:
            async def review():
                return await app._wait_panel(PlanReviewScreen("# 草稿"))

            review_worker = app.run_worker(review())
            target = await self.wait_for_target(pilot, '#plan-edit', focus_id='plan-reject')
            self.assertTrue(await pilot.click('#plan-edit'), f'Click missed {target.region}')
            self.assertEqual(await self.wait_for_result(pilot, review_worker.wait()), 'edit')

            async def edit():
                return await app._wait_panel(PlanEditScreen("# 草稿"))

            edit_worker = app.run_worker(edit())
            target = await self.wait_for_target(pilot, '#plan-edit-save', focus_id='plan-edit-content')
            app.screen.query_one("#plan-edit-content", TextArea).text = "# 已修改"
            self.assertTrue(await pilot.click('#plan-edit-save'), f'Click missed {target.region}')
            self.assertEqual(await self.wait_for_result(pilot, edit_worker.wait()), '# 已修改')

    async def test_dashboard_cost_context_and_compact_commands_use_active_service(self):
        service = FakeService()
        service.session_store = SimpleNamespace(read_events=lambda thread_id: [
            {"kind": "usage", "data": {"input_tokens": 20, "output_tokens": 5, "cache_hit_tokens": 3}},
        ])
        service.get_context_summary = lambda thread_id: {
            "estimated_tokens": 128, "message_count": 4, "user_turns": 2, "pinned_messages": 1,
        }
        service.compact_threshold_tokens = 150_000
        service.compact_context = AsyncMock(return_value={"compacted": True, "before_tokens": 100, "after_tokens": 40})
        app = TerminalAgentApp(service, make_settings(), thread_id="utility-commands")
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app._dispatch("/cost")
            app._dispatch("/context")
            app._dispatch("/compact")
            await pilot.pause(0.2)
            lines = "\n".join(str(line) for line in app.query_one("#transcript", RichLog).lines)
        self.assertIn("25 tokens", lines)
        self.assertIn("128 tokens", lines)
        self.assertIn("100", lines)
        service.compact_context.assert_awaited_once_with("utility-commands")

    async def test_dashboard_plan_command_uses_shared_flow(self):
        service = FakeService()
        service.runtime_factory = SimpleNamespace(plan_store=object())
        app = TerminalAgentApp(service, make_settings(), thread_id="plan-command")
        with patch("tui.run_plan_flow", new_callable=AsyncMock, return_value=True) as flow:
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                app._dispatch("/plan 实现功能")
                await pilot.pause(0.2)
        self.assertEqual(flow.await_count, 1)
        self.assertEqual(flow.await_args.kwargs["thread_id"], "plan-command")
        self.assertIn("实现功能", flow.await_args.kwargs["prompt_message"])

    async def test_dashboard_goal_command_uses_shared_flow_and_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings("key", "https://api.deepseek.com", "deepseek-flash", root)
            goal_store = GoalStore(root / "state" / "goals.json")
            service = FakeService()
            service.runtime_factory = SimpleNamespace(goal_store=goal_store, task_runner=None)
            app = TerminalAgentApp(service, settings, thread_id="goal-command")
            with patch("tui.drive_goal", new_callable=AsyncMock) as flow:
                async with app.run_test(size=(100, 32)) as pilot:
                    await pilot.pause()
                    app._dispatch("/goal --max-rounds 2 --max-cost-usd 0.25 修复项目")
                    await pilot.pause(0.2)
            created = goal_store.latest()

        self.assertEqual(created.max_rounds, 2)
        self.assertEqual(created.max_cost_usd, 0.25)
        self.assertEqual(created.objective, "修复项目")
        self.assertEqual(flow.await_count, 1)

    async def test_tui_service_receives_project_permission_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings("key", "https://api.deepseek.com", "deepseek-chat", Path(directory))
            factory = SimpleNamespace(session_store=SimpleNamespace(list_sessions=lambda: []), close=lambda: None)
            with (
                patch("tui.AgentRuntimeFactory", return_value=factory),
                patch("tui.AgentService", return_value=FakeService()) as service_class,
                patch("tui.TerminalAgentApp") as app_class,
            ):
                run_tui(settings)

        engine = service_class.call_args.kwargs["permission_engine"]
        self.assertEqual(engine.project_root, Path(directory).resolve())
        app_class.return_value.run.assert_called_once()

    async def test_dashboard_shell_shows_banner_metrics_hint_and_composer_placeholder(self):
        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="dashboard-shell")
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            self.assertTrue(app.query_one("#banner", Static).display)
            self.assertIn("deepseek-chat", app.query_one("#composer-stats", Static).content.plain)
            self.assertEqual(len(app.query("#model")), 0)
            self.assertIn("输入消息", str(app.query_one("#hint", Static).content))
            placeholder = app.query_one("#composer-placeholder", Static)
            self.assertTrue(placeholder.display)
            composer = app.query_one("#composer", TextArea)
            composer.text = "正在输入"
            await pilot.pause()
            self.assertFalse(placeholder.display)
            composer.text = ""
            await pilot.pause()
            self.assertTrue(placeholder.display)

    async def test_dashboard_compact_layout_hides_banner_and_hint(self):
        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="compact-shell")
        async with app.run_test(size=(68, 20)) as pilot:
            await pilot.pause()
            self.assertFalse(app.query_one("#banner", Static).display)
            self.assertFalse(app.query_one("#hint", Static).display)

        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="single-banner")
        async with app.run_test(size=(76, 30)) as pilot:
            await pilot.pause()
            self.assertTrue(app.query_one("#banner", Static).display)
            self.assertIn("ignovate harness", str(app.query_one("#banner", Static).content))

    async def test_dashboard_metrics_redact_configured_secret(self):
        settings = Settings("secret-key", "https://api.deepseek.com", "secret-key-model", Path.cwd() / "secret-key")
        app = TerminalAgentApp(FakeService(), settings, thread_id="safe-topbar")
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            content = app.query_one("#composer-stats", Static).content.plain + str(app.query_one("#session-info", Static).content)
            self.assertNotIn("secret-key", content)
            self.assertIn("[密钥已隐藏]", content)

    async def test_dashboard_command_menu_aligns_descriptions_and_contains_hint(self):
        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="dashboard-menu")
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.press("/")
            await pilot.pause()
            text = app.query_one("#command-menu", Static).content.plain
            lines = text.splitlines()
            help_row = next(line for line in lines if "/help" in line)
            init_row = next(line for line in lines if "/init" in line)
            self.assertEqual(help_row.index("显示帮助"), init_row.index("分析项目"))
            self.assertIn("Tab/Enter 选中", lines[-1])

    async def test_command_menu_keeps_composer_visible_on_80_by_24_terminal(self):
        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="small-menu")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("/")
            await pilot.pause()
            menu = app.query_one("#command-menu", Static)
            composer = app.query_one("#composer-frame")
            self.assertFalse(app.query_one("#banner", Static).display)
            self.assertGreater(composer.region.height, 0)
            self.assertLessEqual(composer.region.bottom, app.size.height)
            self.assertLessEqual(menu.region.bottom, composer.region.y)

    async def test_80_by_24_idle_screen_uses_single_line_banner(self):
        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="small-idle")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            banner = app.query_one("#banner", Static)
            self.assertTrue(banner.display)
            self.assertEqual(len(banner.content.plain.splitlines()), 1)
            self.assertLessEqual(app.query_one("#composer-frame").region.bottom, app.size.height)

    async def test_topbar_does_not_repeat_brand_while_banner_is_visible(self):
        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="brand-once")
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            self.assertFalse(app.query_one("#brand", Static).display)
            self.assertTrue(app.query_one("#banner", Static).display)
            await pilot.press("/")
            await pilot.pause()
            self.assertTrue(app.query_one("#brand", Static).display)
            self.assertFalse(app.query_one("#banner", Static).display)

    async def test_command_menu_pages_to_keep_selected_command_visible(self):
        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="paged-menu")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("/")
            await pilot.press(*(["down"] * 12))
            await pilot.pause()
            lines = app.query_one("#command-menu", Static).content.plain.splitlines()
            self.assertLessEqual(len(lines), 7)
            self.assertTrue(any(line.startswith("› /goal") for line in lines), lines)

    async def test_compact_command_menu_uses_short_rows_and_keeps_input_visible(self):
        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="tiny-menu")
        async with app.run_test(size=(50, 18)) as pilot:
            await pilot.press("/")
            await pilot.press(*(["down"] * 12))
            await pilot.pause()
            menu = app.query_one("#command-menu", Static)
            composer = app.query_one("#composer-frame")
            lines = menu.content.plain.splitlines()
            self.assertLessEqual(len(lines), 4)
            self.assertTrue(any(line.startswith("› /goal") for line in lines), lines)
            self.assertLessEqual(menu.region.bottom, composer.region.y)
            self.assertLessEqual(composer.region.bottom, app.size.height)

    async def test_resizing_open_command_menu_keeps_selected_row_visible(self):
        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="resized-menu")
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.press("/")
            await pilot.press(*(["down"] * 12))
            await pilot.resize_terminal(50, 18)
            await pilot.pause()
            menu = app.query_one("#command-menu", Static)
            lines = menu.content.plain.splitlines()
            self.assertLessEqual(len(lines), 4)
            self.assertTrue(any(line.startswith("› /goal") for line in lines), lines)

    async def test_dashboard_transcript_groups_roles_and_keeps_usage_in_composer(self):
        class DashboardService(FakeService):
            async def stream_turn(self, message, config, **kwargs):
                yield TurnEvent("usage", {"input_tokens": 100, "output_tokens": 5, "cache_hit_tokens": 10})
                yield TurnEvent("tool_start", {"call_id": "r1", "name": "read_file", "preview": {"path": "main.py"}})
                yield TurnEvent("tool_end", {"call_id": "r1", "name": "read_file", "ok": True, "elapsed_ms": 3})
                yield TurnEvent("final", {"text": "检查完成"})

        app = TerminalAgentApp(DashboardService(), make_settings(), thread_id="dashboard-events")
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app._dispatch("检查 main.py")
            await pilot.pause(0.2)
            lines = "\n".join(line.text for line in app.query_one("#transcript", RichLog).lines)
            self.assertIn("❯ 检查 main.py", lines)
            self.assertIn("● ignovate harness", lines)
            self.assertIn("✓ 读取 main.py · 3ms", lines)
            self.assertNotIn("tool_", lines)
            self.assertNotIn("工具 1 项", lines)
            self.assertNotIn("╭", lines)
            self.assertNotIn("╰", lines)
            self.assertNotIn("completed", lines)
            self.assertNotIn("step 1", lines)
            self.assertNotIn("tokens im=", lines)
            self.assertEqual(lines.count("检查 main.py"), 1)
            stats = app._status_details()
            self.assertIn("Token 105", stats)
            self.assertIn("100 / 65.5k", app.query_one('#token-weather', Static).content.plain)

    async def test_rewind_confirmation_warns_and_cancellation_does_not_change_state(self):
        class RewindService(FakeService):
            def __init__(self):
                super().__init__()
                self.rewinds = 0

            async def rewind(self, _thread_id):
                self.rewinds += 1
                return 2

        service = RewindService()
        app = TerminalAgentApp(service, make_settings(), thread_id="rewind-ui")

        async with app.run_test(size=(100, 32)) as pilot:
            async def show_screen():
                return await app._wait_panel(RewindConfirmationScreen())

            worker = app.run_worker(show_screen())
            target = await self.wait_for_target(pilot, '#cancel', focus_id='cancel')
            details = app.screen.query_one("#rewind-details", Static).render().plain
            self.assertIn("舍弃", details)
            self.assertIn("不会撤销", details)
            self.assertTrue(await pilot.click('#cancel'), f'Click missed {target.region}')
            self.assertFalse(await self.wait_for_result(pilot, worker.wait()))
            self.assertEqual(service.rewinds, 0)

            app._dispatch("/rewind")
            target = await self.wait_for_target(pilot, '#confirm', focus_id='cancel')
            self.assertTrue(await pilot.click('#confirm'), f'Click missed {target.region}')
            await self.wait_for_result(pilot, app.session_runner.wait_idle())

        self.assertEqual(service.rewinds, 1)

    async def test_turn_renders_stream_events_before_final_answer(self):
        service = FakeStreamingService()
        app = TerminalAgentApp(service, make_settings(), thread_id="streaming-ui")

        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app._dispatch("hi")
            await asyncio.wait_for(service.waiting_for_release.wait(), timeout=3)
            await pilot.pause()
            log = app.query_one("#transcript", RichLog)
            self.assertTrue(any(line.text.strip() == 'Agent' for line in log.lines),
                            '流式内容应在对话中即时可见')
            self.assertIsNotNone(app._live_response)
            service.release.set()
            await pilot.pause(0.1)
            self.assertIsNone(app._live_response)
            lines = "\n".join(str(line) for line in app.query_one("#transcript", RichLog).lines)
            self.assertEqual(lines.count("Agent 回答"), 1)
            self.assertEqual(lines.count("hi"), 1)

        self.assertEqual(len(service.calls), 1)
        self.assertEqual(app.completed_turns, 1)

    async def test_history_replaces_transcript_and_hides_tool_payloads(self):
        class HistoryService(FakeService):
            def get_history(self, thread_id):
                return [
                    ("user", "先前问题"),
                    ("assistant", '[{"type":"tool_call","name":"read_file"}]'),
                    ("tool(read_file)", "工具原始结果"),
                    ("assistant", "先前回答"),
                ]

        app = TerminalAgentApp(HistoryService(), make_settings(), thread_id="history-clean")
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app._dispatch("当前问题")
            await pilot.pause(0.1)
            app._dispatch("/history")
            await pilot.pause()
            lines = "\n".join(str(line) for line in app.query_one("#transcript", RichLog).lines)
            self.assertEqual(lines.count("先前问题"), 1)
            self.assertEqual(lines.count("先前回答"), 1)
            self.assertNotIn("当前问题", lines)
            self.assertNotIn("工具原始结果", lines)
            self.assertNotIn("tool_call", lines)
            self.assertIn("❯ ", lines)
            self.assertIn("ignovate harness", lines)

    async def test_resume_replaces_history_and_updates_fixed_session_totals(self):
        service = FakeService()
        service.session_store = SimpleNamespace(
            resolve_session=lambda _argument: "saved-thread",
            read_events=lambda thread_id: ([
                {"kind": "turn_start"},
                {"kind": "usage", "data": {"input_tokens": 400, "output_tokens": 80, "cache_hit_tokens": 60}},
            ] if thread_id == "saved-thread" else []),
        )
        app = TerminalAgentApp(service, make_settings(), thread_id="new-thread")
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app._dispatch("/resume saved-thread")
            app._dispatch("/count")
            await pilot.pause()
            lines = "\n".join(str(line) for line in app.query_one("#transcript", RichLog).lines)
            self.assertIn("先前问题", lines)
            self.assertIn("先前回答", lines)
            self.assertNotIn("准备就绪", lines)
            self.assertIn("本次运行已完成 0 轮", lines)
            stats = app._status_details()
            self.assertIn("会话 1 轮", stats)
            self.assertIn("Token 480", stats)
            self.assertIn("400 / 65.5k", app.query_one('#token-weather', Static).content.plain)

    async def test_project_command_switches_tools_and_starts_new_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "图书管理系统"
            root.mkdir()
            original_root = local_tools.PROJECT_ROOT
            old_service = FakeService()
            new_service = FakeService()
            app = TerminalAgentApp(old_service, make_settings(), thread_id="old-thread")
            try:
                with (
                    patch("tui.AgentRuntimeFactory") as factory,
                    patch("tui.AgentService", return_value=new_service),
                ):
                    factory.return_value.settings=Settings('unit-test-key','https://api.invalid','deepseek-chat',root.resolve())
                    factory.return_value.preferences=None
                    factory.return_value.skill_registry=None
                    async with app.run_test(size=(100, 32)) as pilot:
                        await pilot.pause()
                        app._dispatch(f"/project {root}")
                        # Project switching now awaits connection/process cleanup.
                        await app.workers.wait_for_complete()
                        self.assertEqual(app.settings.project_root, root.resolve())
                        self.assertEqual(local_tools.PROJECT_ROOT, original_root)
                        self.assertNotEqual(app.thread_id, "old-thread")
                        self.assertIn(root.name, str(app.query_one("#session-info", Static).content))
                        app._dispatch("看一下这个项目")
                        await pilot.pause(0.2)
                    factory.assert_called_once()
                    self.assertEqual(len(new_service.calls), 1)
                    self.assertEqual(old_service.calls, [])
            finally:
                local_tools.PROJECT_ROOT = original_root

    async def test_layout_command_suggestions_and_review_dispatch(self):
        service = FakeService()
        app = TerminalAgentApp(service, make_settings(), thread_id="tui-thread")

        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.query_one("#transcript"), RichLog)
            self.assertIsInstance(app.query_one("#composer"), TextArea)
            self.assertIsInstance(app.query_one("#command-menu"), Static)

            await pilot.press("/", "r")
            await pilot.pause()
            self.assertTrue(app.query_one("#command-menu", Static).display)
            await pilot.press("tab")
            self.assertEqual(app.query_one("#composer", TextArea).text, "/review ")

            await pilot.press("s", "r", "c", "enter")
            await pilot.pause(0.2)
            self.assertEqual(len(service.calls), 1)
            self.assertEqual(service.calls[0]["profile"], "review")
            self.assertEqual(service.calls[0]["target_path"], "src")

    async def test_command_menu_navigation_and_escape(self):
        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="menu-nav")

        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            await pilot.press("/")
            await pilot.pause()
            self.assertTrue(app.query_one("#command-menu", Static).display)

            await pilot.press("down", "enter")
            self.assertEqual(app.query_one("#composer", TextArea).text, "/init")
            self.assertFalse(app.query_one("#command-menu", Static).display)

            composer = app.query_one("#composer", TextArea)
            composer.text = "/"
            composer.cursor_location = (0, 1)
            await pilot.pause()
            self.assertTrue(app.query_one("#command-menu", Static).display)
            await pilot.press("escape")
            await pilot.press("h")
            await pilot.pause()
            self.assertFalse(app.query_one("#command-menu", Static).display)
            self.assertFalse(app.query_one("#composer", TextArea).suggestions_active)

            composer.text = ""
            composer.cursor_location = (0, 0)
            await pilot.pause()
            await pilot.press("/")
            await pilot.pause()
            self.assertTrue(app.query_one("#command-menu", Static).display)

    async def test_init_dispatch_uses_init_profile_and_visible_command(self):
        service = FakeService()
        app = TerminalAgentApp(service, make_settings(), thread_id="init-command")

        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            await pilot.press("/", "i", "enter", "enter")
            await pilot.pause(0.2)

        self.assertEqual(service.calls[0]["message"], "/init")
        self.assertEqual(service.calls[0]["profile"], "init")

    async def test_inline_approval_approves_pending_operation(self):
        service = FakeService(request_approval=True)
        app = TerminalAgentApp(service, make_settings(), thread_id="approval-flow")

        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            await pilot.press("h", "i", "enter")
            await pilot.pause()
            self.assertTrue(app.query_one('#approval-panel').display)
            self.assertEqual(len(app.screen_stack), 1)
            await pilot.press('2')
            await pilot.pause(0.2)

        self.assertEqual(service.decision, "approve")

    async def test_failed_request_does_not_prevent_later_turn(self):
        service = FakeService(failures=1)
        app = TerminalAgentApp(service, make_settings(), thread_id="retry-after-error")

        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            await pilot.press("h", "i", "enter")
            await pilot.pause(0.2)
            await pilot.press("a", "g", "a", "i", "n", "enter")
            await pilot.pause(0.2)

        self.assertEqual(len(service.calls), 2)
        self.assertEqual(app.completed_turns, 1)

    async def test_ctrl_enter_inserts_newline_and_enter_sends(self):
        service = FakeService()
        app = TerminalAgentApp(service, make_settings(), thread_id="input-thread")

        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            await pilot.press("h", "i", "ctrl+enter", "x", "enter")
            await pilot.pause(0.2)

        self.assertEqual(service.calls[0]["message"], "hi\nx")

    async def test_approval_screen_returns_explicit_decision(self):
        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="approval-thread")

        async with app.run_test(size=(100, 32)) as pilot:
            async def show_screen():
                return await app._request_approval(
                        {
                            "name": "write_file",
                            "args": {
                                "path": "notes.txt",
                                "content": "[red]sample[/red]",
                            },
                        },
                        1,
                        1,
                )

            worker = app.run_worker(show_screen())
            await self.wait_for_target(pilot, '#approval-choices', focus_id='approval-choices')
            self.assertIsNotNone(app.screen.query_one("#approval-details"))
            await pilot.press("d")
            await pilot.pause()
            rendered = app.screen.query_one("#approval-details", Static).render()
            self.assertIn("[red]sample[/red]", rendered.plain)
            await pilot.press("1")
            self.assertEqual(await self.wait_for_result(pilot, worker.wait()), 'reject')

    async def test_approval_screen_shows_reason_diff_and_session_allow_choice(self):
        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="approval-details")

        async with app.run_test(size=(100, 32)) as pilot:
            async def show_screen():
                return await app._request_approval(
                        {
                            "name": "edit_file",
                            "args": {"path": "src/main.py", "old_string": "old", "new_string": "new"},
                            "_approval": {
                                "reason": "该编辑需要审批。",
                                "suggested_rule": "Edit(./src/main.py)",
                                "preview": {"path": "src/main.py", "diff": "-old\n+new"},
                            },
                        },
                        1,
                        1,
                )

            worker = app.run_worker(show_screen())
            await self.wait_for_target(pilot, '#approval-choices', focus_id='approval-choices')
            details = app.screen.query_one("#approval-details", Static).render().plain
            self.assertIn("该编辑需要审批", details)
            self.assertIn("-old", details)
            self.assertIn("approve_session", app.query_one("#approval-panel")._decisions)
            await pilot.press("3")
            self.assertEqual(await self.wait_for_result(pilot, worker.wait()), 'approve_session')

    async def test_narrow_terminal_switches_to_compact_layout(self):
        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="small-screen")

        async with app.run_test(size=(50, 18)) as pilot:
            await pilot.pause()
            self.assertTrue(app.query_one("#transcript").has_class("compact"))
            self.assertLessEqual(app.query_one("#composer-frame").region.bottom, app.size.height)



    async def test_dashboard_welcome_card_anchors_the_empty_panel(self):
        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="welcome-card")
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            transcript = "\n".join(
                str(line) for line in app.query_one("#transcript", RichLog).lines
            )
            self.assertIn("本地编码助手", transcript)
            self.assertIn("/plan", transcript)
            self.assertIn("/sessions", transcript)
            self.assertLessEqual(len(transcript.splitlines()), 4)
            self.assertNotIn("会话  sess-", transcript)
            self.assertEqual(app.query_one("#transcript").border_title, " 对话 ")
            self.assertEqual(app.query_one("#composer-frame").border_title, " ❯ 输入 ")

    async def test_dashboard_menu_hint_is_inside_menu_without_extra_footer(self):
        app = TerminalAgentApp(FakeService(), make_settings(), thread_id="footer-menu")
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            self.assertEqual(len(app.query("#footer")), 0)

            composer = app.query_one("#composer", TextArea)
            composer.text = "/"
            await pilot.pause()
            self.assertTrue(app.query_one("#command-menu", Static).display)
            self.assertIn("Tab/Enter 选中", app.query_one("#command-menu", Static).content.plain)

            composer.text = ""
            await pilot.pause()
            self.assertFalse(app.query_one("#command-menu", Static).display)



    async def test_dashboard_tool_group_labels_multiple_tools_and_aligns_rows(self):
        class MultiToolService(FakeService):
            async def stream_turn(self, message, config, **kwargs):
                for index, name in enumerate(("list_files", "glob", "read_file")):
                    yield TurnEvent(
                        "tool_start",
                        {"call_id": f"c{index}", "name": name, "preview": {"path": "main.py"}},
                    )
                    yield TurnEvent(
                        "tool_end",
                        {"call_id": f"c{index}", "name": name, "ok": True, "elapsed_ms": 5 + index},
                    )
                yield TurnEvent("final", {"text": "完成"})

        app = TerminalAgentApp(MultiToolService(), make_settings(), thread_id="multi-tool")
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app._dispatch("看三个文件")
            await pilot.pause(0.2)
            strips = app.query_one("#transcript", RichLog).lines

        visible = [getattr(strip, "text", str(strip)).rstrip() for strip in strips]
        joined = "\n".join(visible)
        self.assertIn("目录 1 · 查找 1 · 读取 1 · 18ms", joined)
        tool_rows = [line for line in visible if line.lstrip().startswith("✓ ")]
        self.assertEqual(len(tool_rows), 0)
        self.assertIn('Ctrl+O 展开', joined)
        self.assertTrue(any("❯ 看三个文件" in line for line in visible), visible)

    async def test_dashboard_failed_tool_row_names_the_failure(self):
        class FailingService(FakeService):
            async def stream_turn(self, message, config, **kwargs):
                yield TurnEvent(
                    "tool_start",
                    {"call_id": "c1", "name": "edit_file", "preview": {"path": "src/a.py"}},
                )
                yield TurnEvent(
                    "tool_end",
                    {
                        "call_id": "c1",
                        "name": "edit_file",
                        "ok": False,
                        "elapsed_ms": 4,
                        "summary": "匹配不唯一",
                    },
                )
                yield TurnEvent("final", {"text": "已停止"})

        app = TerminalAgentApp(FailingService(), make_settings(), thread_id="failed-tool")
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app._dispatch("改一下")
            await pilot.pause(0.2)
            strips = app.query_one("#transcript", RichLog).lines

        joined = "\n".join(getattr(strip, "text", str(strip)) for strip in strips)
        self.assertIn("失败：匹配不唯一", joined)


if __name__ == "__main__":
    unittest.main()
