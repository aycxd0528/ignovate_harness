import os
import asyncio
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from dataclasses import dataclass
from unittest.mock import patch
from io import StringIO

from prompt_toolkit.document import Document
from rich.cells import cell_len
from rich.console import Console as RichConsole

from ui.approval import prompt_approval
from ui.app import _parse_goal_request, run_inline
from ui.banner import BANNER_ROWS
from ui.console import Console
from ui.markdown_stream import MarkdownStreamBuffer
from ui.prompt import AtFileCompleter, RedactingFileHistory, SlashCompleter
from ui.render import (
    _build_toolbar,
    elide_middle,
    render_approval,
    render_diff,
    render_tool_call,
    render_user_message,
)
from ui.theme import Theme, load_theme
from config import Settings
from nailong.core.sessions import ProjectSessionStore
from nailong.core.plan import PlanStore


def render_text(renderable, *, width=80, no_color=True):
    output = StringIO()
    console = RichConsole(
        file=output,
        width=width,
        force_terminal=not no_color,
        color_system="truecolor" if not no_color else None,
        no_color=no_color,
        highlight=False,
    )
    console.print(renderable)
    return output.getvalue()


class FakePromptSession:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.prompts = []

    async def prompt_async(self, message, *, default=None):
        self.prompts.append((message, default))
        return next(self.answers)


class InlinePromptSession:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.toolbars = []
        self.prompts = []

    async def prompt_async(self, message, *, bottom_toolbar=None, default=None):
        self.prompts.append(message)
        self.toolbars.append(bottom_toolbar() if callable(bottom_toolbar) else bottom_toolbar)
        return next(self.answers)


class PlanPromptSession:
    def __init__(self, answers):
        self.answers = iter(answers)

    async def prompt_async(self, message, *, bottom_toolbar=None, default=None):
        return next(self.answers)


class RecordingConsole(Console):
    def __init__(self):
        output = StringIO()
        super().__init__(theme=Theme(no_color=True), console=RichConsole(file=output, no_color=True))
        self.transitions = []

    def pause_live(self):
        super().pause_live()
        self.transitions.append(("pause", self.live_running))

    def resume_live(self):
        super().resume_live()
        self.transitions.append(("resume", self.live_running))


class InlineUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_rewind_keyboard_cancel_keeps_inline_session_open(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            store = ProjectSessionStore(root, base_dir=root/'data')
            class Factory:
                session_store = store
                def __init__(self, settings): pass
                async def aclose(self): pass
                def close(self): pass
            class Prompt(InlinePromptSession):
                async def prompt_async(self, message, **kwargs):
                    self.prompts.append(message)
                    answer = next(self.answers)
                    if isinstance(answer, BaseException): raise answer
                    return answer
            prompt = Prompt(['/rewind', KeyboardInterrupt(), '/exit'])
            output = StringIO()
            console = Console(theme=Theme(no_color=True), console=RichConsole(file=output, no_color=True))
            with patch('ui.app.AgentRuntimeFactory', Factory), patch('ui.app.AgentService'):
                await run_inline(Settings('key', 'https://api.invalid', 'deepseek-flash', root),
                                 prompt_session=prompt, console=console)
            self.assertEqual(len(prompt.prompts), 3)
            self.assertIn('已取消回退', output.getvalue())

    async def test_failed_project_switch_keeps_original_service_and_toolbar(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            root, other = base/'old', base/'new'
            root.mkdir(); other.mkdir()
            store = ProjectSessionStore(root, base_dir=base/'data')
            calls = []
            class Factory:
                session_store = store
                def __init__(self, settings):
                    if settings.project_root == other: raise ValueError('invalid project configuration')
                    self.settings = settings
                    self.closed = False
                async def aclose(self): self.closed = True
                def close(self): self.closed = True
            class Service:
                def __init__(self, factory, **kwargs): self.runtime_factory = factory; self.session_store = store
                async def stream_turn(self, *args, **kwargs):
                    calls.append((self.runtime_factory.settings.project_root, self.runtime_factory.closed))
                    from agent_service import TurnEvent
                    yield TurnEvent('final', {'text':'done'})
            class Prompt(InlinePromptSession):
                async def prompt_async(self, message, **kwargs):
                    await asyncio.sleep(.01)
                    return await super().prompt_async(message, **kwargs)
            prompt = Prompt([f'/project {other}', 'touch file', '/exit'])
            output = StringIO()
            console = Console(theme=Theme(no_color=True), console=RichConsole(file=output, no_color=True))
            with patch('ui.app.AgentRuntimeFactory', Factory), patch('ui.app.AgentService', Service):
                await run_inline(Settings('key', 'https://api.invalid', 'deepseek-flash', root),
                                 prompt_session=prompt, console=console)
            self.assertEqual(calls, [(root, False)])
            self.assertIn('old', str(prompt.toolbars[1]))
            self.assertIn('切换项目失败', output.getvalue())

    async def test_goal_command_parses_user_budget_limits(self):
        self.assertEqual(
            _parse_goal_request('--max-rounds 8 --max-cost-usd=0.35 "refactor CLI"'),
            ("refactor CLI", 8, 0.35),
        )
        with self.assertRaisesRegex(ValueError, "轮数"):
            _parse_goal_request("--max-rounds 101 refactor CLI")

    async def test_rewind_explains_discard_and_requires_confirmation_before_mutating_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            store = ProjectSessionStore(root, base_dir=Path(directory) / "state")

            class Factory:
                session_store = store

                def __init__(self, _settings):
                    pass

                async def aclose(self):
                    pass

                def close(self):
                    pass

            class Service:
                def __init__(self, *_args, **_kwargs):
                    self.rewinds = 0

                async def rewind(self, _thread_id):
                    self.rewinds += 1
                    return 2

            prompt = InlinePromptSession(["/rewind", "n", "/rewind", "y", "/exit"])
            output = StringIO()
            console = Console(theme=Theme(no_color=True), console=RichConsole(file=output, no_color=True))
            settings = Settings("key", "https://api.deepseek.com", "deepseek-chat", root)
            services = []

            def service_factory(*args, **kwargs):
                service = Service(*args, **kwargs)
                services.append(service)
                return service

            with (
                patch("ui.app.AgentRuntimeFactory", Factory),
                patch("ui.app.AgentService", service_factory),
            ):
                await run_inline(settings, prompt_session=prompt, console=console)

        self.assertEqual(services[0].rewinds, 1)
        self.assertIn("确认回退", prompt.prompts[1])
        self.assertIn("确认回退", prompt.prompts[3])
        self.assertIn("不会撤销", output.getvalue())
        self.assertIn("已取消", output.getvalue())

    async def test_inline_app_consumes_stream_and_releases_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            store = ProjectSessionStore(root, base_dir=Path(directory) / "state")
            goal = SimpleNamespace(state="active", round=1, max_rounds=20)

            class Factory:
                session_store = store
                goal_store = SimpleNamespace(active=lambda: goal, latest=lambda: goal)

                def __init__(self, _settings):
                    self.closed = False

                async def aclose(self):
                    self.closed = True

                def close(self):
                    pass

            class Service:
                def __init__(self, *_args, **_kwargs):
                    self.events = []

                async def stream_turn(self, message, config, **kwargs):
                    self.events.append((message, config, kwargs))
                    yield SimpleNamespace(kind="usage", data={"input_tokens": 1000, "output_tokens": 20, "cache_hit_tokens": 200})
                    store.append_event(config['configurable']['thread_id'],'usage',{'input_tokens':1000,'output_tokens':20,'cache_hit_tokens':200})
                    yield SimpleNamespace(kind="tool_start", data={"call_id": "r1", "name": "read_file", "preview": {"path": "main.py"}})
                    yield SimpleNamespace(kind="tool_end", data={"call_id": "r1", "name": "read_file", "ok": True, "elapsed_ms": 7})
                    yield SimpleNamespace(kind="token", data={"text": "流式回答"})
                    yield SimpleNamespace(kind="final", data={"text": "流式回答"})

            prompt = InlinePromptSession(["你好", "/exit"])
            output = StringIO()
            console = Console(
                theme=Theme(no_color=True),
                console=RichConsole(file=output, no_color=True, width=200, height=32),
            )
            settings = Settings("key", "https://api.deepseek.com", "deepseek-chat", root)
            with (
                patch("ui.app.AgentRuntimeFactory", Factory),
                patch("ui.app.AgentService", Service),
            ):
                await run_inline(settings, prompt_session=prompt, console=console)

        rendered = output.getvalue()
        self.assertIn("流式回答", rendered)
        self.assertNotIn("run ", rendered)
        self.assertNotIn("step 1", rendered)
        self.assertNotIn("tokens im=1000 out=20 cache=200", rendered)
        self.assertIn("✓ 读取 main.py", rendered)
        self.assertNotIn("tool_", rendered)
        self.assertNotIn("completed · 1 steps", rendered)
        self.assertEqual(rendered.count(BANNER_ROWS[0]), 1)
        self.assertIn("deepseek-chat", prompt.toolbars[0])
        self.assertIn("目标 active 1/20 轮", prompt.toolbars[0])
        self.assertIn("cache 200", prompt.toolbars[1])
        self.assertIn("ctx:1.5%", prompt.toolbars[1])
        weather = prompt.toolbars[1].splitlines()[-1]
        self.assertIn("Clear 2%", weather)
        self.assertIn("1k / 65.5k", weather)
        self.assertIn("+1k", weather)
        self.assertNotIn("Clear", rendered)

    async def test_plan_rejection_keeps_draft_off_disk_and_never_starts_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            store = ProjectSessionStore(root, base_dir=Path(directory) / "state")

            class Factory:
                session_store = store

                def __init__(self, settings):
                    self.plan_store = PlanStore(settings.project_root)

                async def aclose(self):
                    pass

                def close(self):
                    pass

            class Service:
                def __init__(self, factory, *_args, **_kwargs):
                    self.factory = factory
                    self.profiles = []

                async def stream_turn(self, message, config, **kwargs):
                    self.profiles.append(kwargs.get("profile"))
                    plan_id = self.factory.plan_store.stage("# Plan\nChange one file.")
                    yield SimpleNamespace(kind="plan_ready", data={"plan_id": plan_id})
                    yield SimpleNamespace(kind="final", data={"text": "计划已提交。"})

            prompt = PlanPromptSession(["/plan change", "d", "/exit"])
            output = StringIO()
            console = Console(theme=Theme(no_color=True), console=RichConsole(file=output, no_color=True))
            settings = Settings("key", "https://api.deepseek.com", "deepseek-chat", root)
            services = []

            def service_factory(*args, **kwargs):
                service = Service(*args, **kwargs)
                services.append(service)
                return service

            with patch("ui.app.AgentRuntimeFactory", Factory), patch("ui.app.AgentService", service_factory):
                await run_inline(settings, prompt_session=prompt, console=console)

            self.assertEqual(services[0].profiles, ["plan"])
            self.assertFalse((root / ".nailong" / "plans").exists())

    async def test_approved_edited_plan_is_saved_and_injected_into_same_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            store = ProjectSessionStore(root, base_dir=Path(directory) / "state")

            class Factory:
                session_store = store

                def __init__(self, settings):
                    self.plan_store = PlanStore(settings.project_root)

                async def aclose(self):
                    pass

                def close(self):
                    pass

            class Service:
                def __init__(self, factory, *_args, **_kwargs):
                    self.factory = factory
                    self.calls = []

                async def stream_turn(self, message, config, **kwargs):
                    self.calls.append((message, config, kwargs))
                    if kwargs.get("profile") == "plan":
                        plan_id = self.factory.plan_store.stage("# Draft plan")
                        yield SimpleNamespace(kind="plan_ready", data={"plan_id": plan_id})
                    yield SimpleNamespace(kind="final", data={"text": "完成。"})

            prompt = PlanPromptSession(["/plan feature", "e", "y", "/exit"])
            output = StringIO()
            console = Console(theme=Theme(no_color=True), console=RichConsole(file=output, no_color=True))
            settings = Settings("key", "https://api.deepseek.com", "deepseek-chat", root)
            services = []

            def service_factory(*args, **kwargs):
                service = Service(*args, **kwargs)
                services.append(service)
                return service

            with (
                patch("ui.app.AgentRuntimeFactory", Factory),
                patch("ui.app.AgentService", service_factory),
                patch("ui.input_broker.InputBroker.edit", return_value="# Edited plan"),
            ):
                await run_inline(settings, prompt_session=prompt, console=console)

            self.assertEqual(len(services[0].calls), 2, output.getvalue())
            execution = services[0].calls[1]
            self.assertEqual(execution[2]["profile"], "chat")
            self.assertTrue(execution[2]["pin_message"])
            self.assertIn("# Edited plan", execution[0])
            plans = list((root / ".nailong" / "plans").glob("*.md"))
            self.assertEqual(len(plans), 1)
            self.assertIn("# Edited plan", plans[0].read_text(encoding="utf-8"))

    async def test_approval_pauses_rich_before_prompt_and_enter_defaults_to_reject(self):
        key = "api-key-sentinel"
        action = {
            "name": "write_file",
            "args": {"path": "notes.txt", "content": f"body {key}"},
            "_approval": {
                "reason": "文件写入需要确认",
                "preview": {"path": "notes.txt", "diff": "-old\n+new"},
            },
        }
        prompt = FakePromptSession(["d", ""])
        console = RecordingConsole()
        with console.working():
            console.transitions.clear()
            decision = await prompt_approval(prompt, console, action, api_key=key)
            self.assertTrue(console.live_running)

        self.assertEqual(decision.kind, "reject")
        self.assertEqual(prompt.prompts, [("  允许执行吗？[y/n/a/d] ", "n")] * 2)
        self.assertEqual(
            console.transitions,
            [("pause", False), ("resume", True), ("pause", False)],
        )
        self.assertNotIn(key, console.console.file.getvalue())

    async def test_approval_y_and_a_have_explicit_decisions(self):
        console = RecordingConsole()
        with console.working():
            once = await prompt_approval(FakePromptSession(["y"]), console, {"name": "run_command", "args": {}})
            session = await prompt_approval(FakePromptSession(["a"]), console, {"name": "run_command", "args": {}})
        self.assertEqual(once.kind, "approve_once")
        self.assertEqual(session.kind, "approve_session")


class RenderTests(unittest.TestCase):
    def test_tool_output_wraps_at_common_terminal_widths(self):
        renderable = render_tool_call(
            {"name": "read_file", "preview": {"path": "项目/源码/" + "a" * 100 + ".py"}},
            width=80,
            theme=Theme(no_color=True),
        )
        for width in (80, 60):
            rendered = render_text(renderable, width=width)
            self.assertTrue(all(cell_len(line) <= width for line in rendered.splitlines()))

    def test_diff_is_readable_without_ansi_and_long_paths_keep_both_ends(self):
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            theme = load_theme()
        diff = render_diff(
            "old line\n",
            "new line\n",
            "/very/long/project/path/with/a/deeply/nested/module.py",
            width=60,
            theme=theme,
        )
        rendered = render_text(diff, width=60)
        self.assertIn("old line", rendered)
        self.assertIn("new line", rendered)
        self.assertNotIn("\x1b[", rendered)
        elided = elide_middle("项目/路径/非常长的文件名称/模块.py", 14)
        self.assertLessEqual(cell_len(elided), 14)
        self.assertTrue(elided.endswith("py"))

    def test_approval_render_redacts_api_key(self):
        key = "api-key-sentinel"
        rendered = render_text(
            render_approval(
                {"name": "write_file", "args": {"path": "a.txt"}, "_approval": {"reason": key}},
                api_key=key,
                theme=Theme(no_color=True),
            )
        )
        self.assertNotIn(key, rendered)
        self.assertIn("[密钥已隐藏]", rendered)

    def test_theme_falls_back_to_ascii_for_limited_encodings_and_no_color(self):
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            theme = load_theme(encoding="ascii")
        self.assertEqual(theme.glyph_running, ">")
        self.assertTrue(theme.no_color)


class MarkdownStreamTests(unittest.TestCase):
    def test_releases_paragraphs_at_blank_lines(self):
        buffer = MarkdownStreamBuffer()
        self.assertEqual(buffer.feed("Hello"), [])
        self.assertEqual(buffer.feed(" world\n"), [])
        self.assertEqual(buffer.feed("\nNext"), ["Hello world"])
        self.assertEqual(buffer.flush(), "Next")

    def test_releases_closed_fences_as_single_block(self):
        buffer = MarkdownStreamBuffer()
        self.assertEqual(buffer.feed("```python\nprint(1)\n"), [])
        self.assertEqual(buffer.feed("```\n"), ["```python\nprint(1)\n```"])
        self.assertEqual(buffer.flush(), "")

    def test_unclosed_fence_stays_buffered_until_flush(self):
        buffer = MarkdownStreamBuffer()
        self.assertEqual(buffer.feed("```py\nx = 1"), [])
        self.assertEqual(buffer.flush(), "```py\nx = 1")

    def test_fence_after_paragraph_splits_paragraph_first(self):
        buffer = MarkdownStreamBuffer()
        self.assertEqual(
            buffer.feed("intro\n```py\nx\n```"),
            ["intro", "```py\nx\n```"],
        )

    def test_oversized_buffer_is_forced_out_at_newline(self):
        buffer = MarkdownStreamBuffer(max_block_chars=100)
        blocks = buffer.feed("a" * 120 + "\n")
        self.assertTrue(blocks)
        self.assertTrue(all(len(block) <= 100 for block in blocks))

    def test_crlf_input_is_normalized_to_boundaries(self):
        buffer = MarkdownStreamBuffer()
        self.assertEqual(buffer.feed("第一段\r\n\r\n第二段"), ["第一段"])
        self.assertEqual(buffer.flush(), "第二段")

    def test_open_fence_is_forced_out_once_oversized(self):
        buffer = MarkdownStreamBuffer(max_block_chars=50)
        blocks = buffer.feed("```python\n" + "x = 1\n" * 40)
        self.assertTrue(blocks)
        self.assertEqual(buffer.flush(), "")

    def test_flush_of_empty_buffer_is_empty(self):
        buffer = MarkdownStreamBuffer()
        self.assertEqual(buffer.flush(), "")
        self.assertEqual(buffer.feed(""), [])


class ConsoleMarkdownTests(unittest.TestCase):
    def test_streamed_markdown_is_rendered_instead_of_raw_markup(self):
        output = StringIO()
        console = Console(
            theme=Theme(no_color=True),
            console=RichConsole(file=output, no_color=True),
        )
        text = "# 标题\n\n**粗体** 与 `code`\n\n```python\nprint(1)\n```"
        with console.working():
            console.print_event(SimpleNamespace(kind="token", data={"text": text}))
            console.print_event(SimpleNamespace(kind="final", data={"text": text}))
        rendered = output.getvalue()
        self.assertIn("标题", rendered)
        self.assertIn("粗体", rendered)
        self.assertIn("print(1)", rendered)
        self.assertNotIn("**", rendered)
        self.assertNotIn("```", rendered)
        # The final event must not duplicate already-buffered text.
        self.assertEqual(rendered.count("标题"), 1)


class ConsoleToolCardTests(unittest.TestCase):
    def test_detailed_tool_end_prints_diff_and_output_preview(self):
        output = StringIO()
        console = Console(
            theme=Theme(no_color=True),
            console=RichConsole(file=output, no_color=True),
        )
        console.output_style = 'detailed'
        with console.working():
            console.print_event(
                SimpleNamespace(
                    kind="tool_end",
                    data={
                        "name": "edit_file",
                        "ok": True,
                        "summary": "替换 1 处",
                        "path": "src/a.py",
                        "elapsed_ms": 12,
                        "diff": "--- before\n+++ after\n-old\n+new",
                    },
                )
            )
            console.print_event(
                SimpleNamespace(
                    kind="tool_end",
                    data={
                        "name": "run_command",
                        "ok": True,
                        "summary": "命令输出 24 个字符",
                        "elapsed_ms": 8,
                        "output_snippet": "…\nline4",
                    },
                )
            )
        rendered = output.getvalue()
        self.assertIn("-old", rendered)
        self.assertIn("+new", rendered)
        self.assertIn("输出预览：", rendered)
        self.assertIn("line4", rendered)


class SessionInteractionTests(unittest.IsolatedAsyncioTestCase):
    def _saved_store(self, directory: Path, thread_id: str) -> ProjectSessionStore:
        store = ProjectSessionStore(directory, base_dir=directory / "state")
        store.ensure_directories()
        store.append_event(thread_id, "turn_start", {"message_ids": []})
        store.append_event(thread_id, "final", {"text": "上次的对话结论"})
        store.append_event(
            thread_id, "usage", {"input_tokens": 10, "output_tokens": 5, "cache_hit_tokens": 2}
        )
        return store

    def _fakes(self, store):
        class Factory:
            session_store = store
            goal_store = None
            task_runner = SimpleNamespace(
                usage_estimate=lambda tid: {"input_tokens": 0, "output_tokens": 0}
            )

            def __init__(self, _settings):
                self.closed = False

            async def aclose(self):
                self.closed = True

            def close(self):
                pass

        class Service:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_history(self, thread_id):
                return [("user", "你好"), ("assistant", "恢复的历史")]

            async def stream_turn(self, message, config, **kwargs):
                yield SimpleNamespace(kind="final", data={"text": "完成。"})
                return

        return Factory, Service

    async def _drive(self, root, store, answers):
        prompt = InlinePromptSession(answers)
        output = StringIO()
        console = Console(theme=Theme(no_color=True), console=RichConsole(file=output, no_color=True))
        settings = Settings("key", "https://api.deepseek.com", "deepseek-chat", root)
        factory_cls, service_cls = self._fakes(store)
        with (
            patch("ui.app.AgentRuntimeFactory", factory_cls),
            patch("ui.app.AgentService", service_cls),
        ):
            await run_inline(settings, prompt_session=prompt, console=console)
        return prompt, output.getvalue()

    async def test_startup_offers_continue_and_restores_on_yes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "proj"
            root.mkdir()
            saved_id = uuid.uuid4().hex
            store = self._saved_store(root, saved_id)
            prompt, rendered = await self._drive(root, store, ["y", "/exit"])
        self.assertIn("继续上次会话", prompt.prompts[0])
        self.assertIn("已恢复会话", rendered)
        self.assertIn("恢复的历史", rendered)

    async def test_startup_decline_keeps_a_new_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "proj"
            root.mkdir()
            store = self._saved_store(root, uuid.uuid4().hex)
            prompt, rendered = await self._drive(root, store, ["n", "/exit"])
        self.assertIn("继续上次会话", prompt.prompts[0])
        self.assertNotIn("已恢复会话", rendered)

    async def test_sessions_command_renders_table_with_stats(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "proj"
            root.mkdir()
            store = self._saved_store(root, uuid.uuid4().hex)
            _prompt, rendered = await self._drive(root, store, ["n", "/sessions", "/exit"])
        self.assertIn("轮数", rendered)
        self.assertIn("tokens", rendered)
        self.assertIn("1", rendered)
        self.assertIn("15", rendered)  # 10 input + 5 output tokens

    async def test_resume_without_argument_prompts_for_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "proj"
            root.mkdir()
            saved_id = uuid.uuid4().hex
            store = self._saved_store(root, saved_id)
            prompt, rendered = await self._drive(root, store, ["n", "/resume", "1", "/exit"])
        self.assertIn("选择会话序号", prompt.prompts[2])
        self.assertIn("已恢复会话", rendered)


    def test_headings_render_left_aligned_not_centered(self):
        output = StringIO()
        console = Console(
            theme=Theme(no_color=True),
            console=RichConsole(file=output, no_color=True, width=80),
        )
        with console.working():
            console.print_event(
                SimpleNamespace(kind="token", data={"text": "# 标题\n\n正文"})
            )
            console.print_event(SimpleNamespace(kind="final", data={"text": ""}))
        first = next(line for line in output.getvalue().splitlines() if line.strip())
        heading = next(line for line in output.getvalue().splitlines() if "标题" in line)
        self.assertTrue(heading.startswith("  标题"), repr(heading))


class RenderUserMessageTests(unittest.TestCase):
    def test_user_message_redacts_key_and_keeps_prompt_prefix(self):
        key = "echo-key-sentinel"
        rendered = render_text(
            render_user_message(f"包含 {key} 的输入", api_key=key, theme=Theme(no_color=True))
        )
        self.assertNotIn(key, rendered)
        self.assertIn("[密钥已隐藏]", rendered)
        self.assertTrue(rendered.startswith("❯ "))


class ToolbarTests(unittest.TestCase):
    def _toolbar(self, **overrides):
        kwargs = {
            "model": "deepseek-chat",
            "permission_mode": "default",
            "project": "project-name",
            "tokens": 1234,
            "cost_status": " · 约 $0.000456",
            "goal_status": "目标 active 1/20 轮",
            "session_id": "a" * 32,
            "width": 200,
        }
        kwargs.update(overrides)
        return _build_toolbar(**kwargs)

    def test_wide_terminal_keeps_every_segment(self):
        rendered = self._toolbar()
        self.assertIn("deepseek-chat", rendered)
        self.assertIn("目标 active 1/20 轮", rendered)
        self.assertIn("会话 aaaaaaaa", rendered)

    def test_wide_terminal_shows_cache_and_context_when_known(self):
        rendered = self._toolbar(cache_hit_tokens=200, context_usage_percent="1.5%")
        self.assertIn("cache 200", rendered)
        self.assertIn("ctx:1.5%", rendered)

    def test_narrow_terminal_drops_low_priority_segments(self):
        rendered = self._toolbar(width=40)
        self.assertLessEqual(cell_len(rendered), 40)
        self.assertIn("deepseek-chat", rendered)
        self.assertNotIn("会话", rendered)
        self.assertNotIn("目标", rendered)

    def test_model_segment_survives_even_very_narrow_width(self):
        rendered = self._toolbar(width=10)
        self.assertIn("deepseek-chat", rendered)
        self.assertNotIn("default", rendered)


class PromptTests(unittest.TestCase):
    def test_slash_completion_and_at_file_completion_are_scoped(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("pass", encoding="utf-8")
            (root / ".env").write_text("secret", encoding="utf-8")
            (root / "escape").symlink_to(outside)

            slash = list(SlashCompleter({"/review": "review", "/rewind": "rewind"}).get_completions(Document("/re"), None))
            paths = list(AtFileCompleter(root).get_completions(Document("@src/m"), None))
            root_paths = list(AtFileCompleter(root).get_completions(Document("@"), None))

        self.assertEqual([item.text for item in slash], ["/review", "/rewind"])
        self.assertEqual([item.text for item in paths], ["src/main.py"])
        self.assertNotIn(".env", [item.text for item in root_paths])
        self.assertNotIn("escape/", [item.text for item in root_paths])

    def test_at_completion_works_mid_sentence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("pass", encoding="utf-8")
            completions = list(
                AtFileCompleter(root).get_completions(Document("请查看 @src/m"), None)
            )
        self.assertEqual([item.text for item in completions], ["src/main.py"])

    def test_at_completion_lists_directories_first(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "zeta.txt").write_text("pass", encoding="utf-8")
            (root / "alpha").mkdir()
            completions = [
                item.text for item in AtFileCompleter(root).get_completions(Document("@"), None)
            ]
        self.assertEqual(completions[0], "alpha/")
        self.assertEqual(completions[1], "zeta.txt")

    def test_slash_completion_still_requires_line_start(self):
        completions = list(
            SlashCompleter({"/review": "review"}).get_completions(Document("请看 /re"), None)
        )
        self.assertEqual(completions, [])

    def test_file_history_redacts_key_and_sets_private_permissions(self):
        key = "history-secret-sentinel"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history"
            history = RedactingFileHistory(str(path), api_key=key)
            history.append_string(f"please ignore {key}")
            content = path.read_text(encoding="utf-8")
            mode = path.stat().st_mode & 0o777

        self.assertNotIn(key, content)
        self.assertIn("[密钥已隐藏]", content)
        self.assertEqual(mode, 0o600)

    def test_file_history_sanitizes_existing_legacy_entries(self):
        key = "legacy-history-secret"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history"
            from prompt_toolkit.history import FileHistory

            FileHistory(str(path)).append_string(f"old entry {key}")
            history = RedactingFileHistory(str(path), api_key=key)
            loaded = list(history.load_history_strings())
            content = path.read_text(encoding="utf-8")

        self.assertEqual(loaded, ["old entry [密钥已隐藏]"])
        self.assertNotIn(key, content)
