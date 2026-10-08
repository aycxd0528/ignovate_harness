import json
import tempfile
import unittest
from pathlib import Path

from rich.cells import cell_len

from agent_service import TurnEvent
from ui.banner import banner_renderable
from ui.commands import COMMANDS, COMMAND_BY_NAME
from ui.presentation import (
    SessionMetrics,
    StepTracker,
    configured_context_window,
    context_percent,
    format_tool_args,
    render_composer_metrics,
    render_completion,
    render_run_header,
    render_tool_line,
)
from ui.theme import Theme


class DashboardPresentationTests(unittest.TestCase):
    def test_session_metrics_restore_usage_and_fit_narrow_composer(self):
        metrics = SessionMetrics.from_events([
            {"kind": "turn_start"},
            {"kind": "usage", "data": {"input_tokens": 1261, "output_tokens": 144, "cache_hit_tokens": 1024}},
            {"kind": "usage", "data": {"input_tokens": 3117, "output_tokens": 488, "cache_hit_tokens": 2176}},
        ])
        self.assertEqual((metrics.turns, metrics.input_tokens, metrics.output_tokens, metrics.cache_hit_tokens), (1, 4378, 632, 3200))
        self.assertEqual(metrics.last_input_tokens, 3117)
        narrow = render_composer_metrics(metrics, model="deepseek-chat", context_window=65536, width=47).plain
        self.assertIn("会话 1 轮", narrow)
        self.assertIn("Token 5,010", narrow)
        self.assertIn("上下文 3,117/65,536 (4.8%)", narrow)
        self.assertTrue(all(cell_len(line) <= 47 for line in narrow.splitlines()))
        blank = render_composer_metrics(SessionMetrics(), model="deepseek-chat", context_window=65536, width=47).plain
        self.assertIn("上下文 —", blank)

    def test_step_tracker_renders_usage_tools_and_completion_in_event_order(self):
        tracker = StepTracker(context_window=100_000, api_key="secret-key")
        rows = []
        for event in (
            TurnEvent("usage", {"input_tokens": 1000, "output_tokens": 20, "cache_hit_tokens": 200}),
            TurnEvent("tool_start", {"call_id": "r1", "name": "read_file", "preview": {"path": "secret-key.py"}}),
            TurnEvent("tool_end", {"call_id": "r1", "name": "read_file", "ok": True, "elapsed_ms": 7, "summary": "读取成功"}),
            TurnEvent("usage", {"input_tokens": 2000, "output_tokens": 30, "cache_hit_tokens": 300}),
            TurnEvent("final", {"text": "完成"}),
        ):
            rows.extend(tracker.observe(event))

        plain = [row.plain for row in rows]
        self.assertEqual(plain[0], "模型调用 1")
        self.assertIn("tokens im=1000 out=20 cache=200 ctx:1.0%", plain[1])
        self.assertIn("✓ 读取", plain[2])
        self.assertNotIn("tool_", plain[2])
        self.assertIn("[密钥已隐藏]", plain[2])
        self.assertIn("7ms", plain[2])
        self.assertEqual(plain[4], "模型调用 2")
        self.assertIn("ctx:2.0%", plain[5])
        self.assertIn("完成 · 2 次模型调用", plain[6])
        self.assertEqual(tracker.steps, 2)

    def test_step_tracker_falls_back_to_tool_start_when_usage_is_missing(self):
        tracker = StepTracker()
        rows = []
        for event in (
            TurnEvent("tool_start", {"call_id": "a", "name": "read_file", "preview": {"path": "a.py"}}),
            TurnEvent("tool_start", {"call_id": "b", "name": "read_file", "preview": {"path": "b.py"}}),
            TurnEvent("tool_end", {"call_id": "a", "name": "read_file", "ok": True}),
            TurnEvent("tool_end", {"call_id": "b", "name": "read_file", "ok": True}),
            TurnEvent("final", {"text": "完成"}),
        ):
            rows.extend(tracker.observe(event))

        plain = [row.plain for row in rows]
        self.assertEqual(plain.count("模型调用 1"), 1)
        self.assertEqual(plain.count("tokens -"), 1)
        self.assertEqual(tracker.steps, 1)

    def test_renderers_redact_and_respect_cell_width(self):
        header = render_run_header("run-1", "检查 secret-key", api_key="secret-key")
        tool = render_tool_line(
            {"name": "read_file", "preview": {"path": "目录/" + "很长" * 40 + "/secret-key.py"}, "ok": False},
            width=48,
            api_key="secret-key",
        )
        self.assertNotIn("secret-key", header.plain + tool.plain)
        self.assertIn("失败", tool.plain)
        self.assertLessEqual(cell_len(tool.plain), 48)
        self.assertEqual(render_completion(3, ok=False).plain, "✗ 已停止 · 3 次模型调用")
        self.assertEqual(format_tool_args("read_file", {"path": "目录/a.py"}), "目录/a.py")

    def test_context_window_config_is_project_scoped_and_validated(self):
        self.assertEqual(context_percent({"input_tokens": 1000}, 100_000), "1.0%")
        self.assertIsNone(context_percent({"input_tokens": 1000}, None))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / ".nailong" / "settings.json"
            settings.parent.mkdir()
            settings.write_text(json.dumps({"models": {"my-model": {"context_window": 8192}}}), encoding="utf-8", newline='\n')
            self.assertEqual(configured_context_window("my-model", root), 8192)
            settings.write_text(json.dumps({"models": {"my-model": {"context_window": -1}}}), encoding="utf-8", newline='\n')
            self.assertIsNone(configured_context_window("my-model", root))
            self.assertEqual(configured_context_window("deepseek-chat", root), 65536)

    def test_banner_degrades_and_command_table_covers_both_interfaces(self):
        theme = Theme(no_color=True)
        full = banner_renderable(width=100, height=30, theme=theme)
        self.assertEqual(len(full.plain.splitlines()), 6)
        self.assertIn("ignovate harness", banner_renderable(width=76, height=30, theme=theme).plain)
        self.assertIsNone(banner_renderable(width=60, height=30, theme=theme))
        self.assertIsNone(banner_renderable(width=100, height=20, theme=theme))
        self.assertIsNone(banner_renderable(width=100, height=30, enabled=False))
        self.assertTrue({"/plan", "/goal", "/cost", "/context", "/compact"}.issubset(COMMAND_BY_NAME))
        self.assertEqual(len(COMMANDS), len(COMMAND_BY_NAME))
        from ui.app import COMMANDS as inline_commands
        from tui import COMMANDS as textual_commands
        self.assertEqual(set(inline_commands), {command.name for command in textual_commands})


class WelcomeCardTests(unittest.TestCase):
    def test_welcome_card_shows_shortcuts_without_repeating_topbar_and_redacts_key(self):
        from ui.presentation import render_welcome

        card = render_welcome(
            project="/tmp/secret-key-project",
            model="deepseek-chat",
            thread_id="abcdef123456",
            theme=Theme(no_color=True),
            api_key="secret-key",
        )
        plain = card.plain
        self.assertNotIn("secret-key", plain)
        self.assertIn("[密钥已隐藏]", plain)
        self.assertIn("/plan", plain)
        self.assertIn("/sessions", plain)
        self.assertNotIn("sess-abcdef12", plain)
        self.assertNotIn("deepseek-chat", plain)
        self.assertLessEqual(len(plain.splitlines()), 4)


class TranscriptStyleTests(unittest.TestCase):
    def test_role_headers_use_gutter_marker(self):
        from io import StringIO
        from rich.console import Console as RichConsole
        from ui.presentation import render_role_header

        themed = Theme(no_color=True)
        self.assertEqual(render_role_header("user", theme=themed).plain, "❯ 你")
        self.assertEqual(render_role_header("assistant", theme=themed).plain, "● ignovate harness")
        self.assertEqual(render_role_header("error", theme=themed).plain, "✗ 错误")

        buffer = StringIO()
        RichConsole(file=buffer, width=20, no_color=True).print(render_role_header("user", theme=themed))
        self.assertNotIn("\x1b[", buffer.getvalue())

    def test_role_header_and_bullet_fall_back_to_ascii(self):
        from ui.presentation import render_role_header
        from ui.theme import load_theme

        ascii_theme = load_theme(ascii_only=True)
        self.assertEqual(render_role_header("user", theme=ascii_theme).plain, "> 你")
        self.assertEqual(ascii_theme.bullet, "-")

    def test_tool_group_header_labels_count_and_total(self):
        from ui.presentation import render_tool_group_header

        self.assertEqual(
            render_tool_group_header(3, 18, theme=Theme(no_color=True)).plain, "工具 3 项 · 18ms"
        )
        self.assertEqual(
            render_tool_group_header(4, None, theme=Theme(no_color=True)).plain, "工具 4 项"
        )

    def test_tool_args_show_values_without_keys_or_quotes(self):
        self.assertEqual(format_tool_args("read_file", {"path": "main.py"}), "main.py")
        # 参数按固定键序输出，顺序稳定即可
        self.assertEqual(
            format_tool_args("grep", {"pattern": "def run", "path": "src"}), "src · def run"
        )
        self.assertEqual(format_tool_args("read_file", None), "")
        self.assertEqual(format_tool_args("read_file", {}), "")
        clipped = format_tool_args("read_file", {"path": "目录/" + "很长" * 40}, limit=20)
        self.assertLessEqual(cell_len(clipped), 20)

    def test_indent_shifts_text_and_markdown_bodies(self):
        from io import StringIO
        from rich.console import Console as RichConsole
        from rich.markdown import Markdown
        from rich.text import Text
        from ui.presentation import indent

        buffer = StringIO()
        console = RichConsole(file=buffer, width=40, no_color=True)
        console.print(indent(Text("你好")))
        console.print(indent(Markdown("正文"), 4))
        rendered = buffer.getvalue().splitlines()
        self.assertTrue(rendered[0].startswith("  你好"), repr(rendered[0]))
        self.assertTrue(rendered[1].startswith("    正文"), repr(rendered[1]))
