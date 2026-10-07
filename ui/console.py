"""Rich terminal output and explicit ownership of the transient Live line."""

from __future__ import annotations

from contextlib import contextmanager
import time
from typing import Iterator

from rich.console import Console as RichConsole, Group
from rich.live import Live
from rich.markdown import Heading, Markdown
from rich.spinner import Spinner
from rich.text import Text

# Rich centers H1 by default; left-aligned headings read better in an
# inline coding-agent terminal (Claude Code style).
Heading.LEVEL_ALIGN["h1"] = "left"

from ui.markdown_stream import MarkdownStreamBuffer
from ui.presentation import StepTracker, indent, render_completion, render_role_header, render_tool_rows
from ui.render import (
    render_status,
    render_tool_call,
)
from ui.theme import Theme, load_theme, semantic_rich_theme
from ui.activity import ToolGroup


class Console:
    def __init__(self, *, theme: Theme | None = None, console: RichConsole | None = None):
        self.theme = theme or load_theme()
        self.output_style = "normal"
        self.console = console or RichConsole(
            no_color=self.theme.no_color,
            highlight=False,
            soft_wrap=True,
        )
        self._live: Live | None = None
        self._live_running = False
        self._received_chars = 0
        self._markdown = MarkdownStreamBuffer()
        self._status = "ready"
        self._started_at = 0.0
        self._step_tracker: StepTracker | None = None
        self._assistant_open = False
        self._delivery_text = ''
        self._api_key = ""
        self._tool_group = None
        self.apply_theme(self.theme)

    def apply_theme(self,theme):
        self.theme=theme
        if getattr(self,'_semantic_theme_pushed',False): self.console.pop_theme()
        self.console.push_theme(semantic_rich_theme(theme))
        self._semantic_theme_pushed=True

    @property
    def live_running(self) -> bool:
        return self._live_running

    @contextmanager
    def working(self, *, context_window: int | None = None, api_key: str = "") -> Iterator["Console"]:
        """Reserve Rich Live only while the model is working."""
        self._received_chars = 0
        self._markdown = MarkdownStreamBuffer()
        self._started_at = time.monotonic()
        self._assistant_open = False
        self._delivery_text = ''
        self._api_key = api_key
        self._tool_group = None
        self._step_tracker = StepTracker(
            context_window=context_window,
            api_key=api_key,
            theme=self.theme,
            width=self.console.width,
        )
        self._live = Live(
            self._thinking_renderable(),
            console=self.console,
            refresh_per_second=10,
            transient=True,
        )
        self.resume_live()
        try:
            yield self
        finally:
            self.pause_live()
            self._flush_markdown()
            self._flush_tools()
            self._live = None
            self._step_tracker = None

    def pause_live(self) -> None:
        if self._live is not None and self._live_running:
            self._live.stop()
            self._live_running = False

    def resume_live(self) -> None:
        if self._live is not None and not self._live_running:
            self._live.start()
            self._live_running = True

    def set_status(self, status: str) -> None:
        self._status = status
        if self._live is not None and self._live_running:
            if status == "thinking":
                self._live.update(self._thinking_renderable())
            else:
                self._live.update(render_status(status, theme=self.theme))

    def _thinking_renderable(self):
        elapsed = max(0.0, time.monotonic() - self._started_at)
        label = Text(f"思考中 · {elapsed:.1f}s · {self._received_chars} 字")
        preview = self._markdown.preview(60).replace("**", "").replace("`", "").lstrip("#>- ")
        if preview:
            label.append(f" · {preview}")
        if self.theme.no_color:
            return Group(Text(self.theme.glyph_running + " "), label)
        return Group(Spinner("dots", style=self.theme.accent), label)

    def print(self, renderable, *, markup: bool = False, end: str = "\n") -> None:
        self.console.print(renderable, markup=markup, end=end)

    def print_markdown(self, text: str) -> None:
        """Render a static Markdown string with syntax-highlighted code blocks."""
        if text:
            was_live = self._live_running
            if was_live:
                self.pause_live()
            try:
                self.print(indent(Markdown(text, code_theme=self.theme.code_theme, style="none" if self.theme.no_color else self.theme.foreground)))
            finally:
                if was_live:
                    self.resume_live()

    def _flush_markdown(self) -> None:
        remainder = self._markdown.flush()
        if remainder.strip():
            self.print_markdown(remainder)

    def print_event(self, event) -> None:
        kind = getattr(event, "kind", "")
        data = getattr(event, "data", {}) or {}
        tracker = self._step_tracker
        rows = tracker.observe(event) if tracker is not None else []
        if kind == "status":
            self.set_status(data.get("status", "thinking"))
        elif kind == "usage":
            if self.output_style == "detailed":
                self._flush_markdown()
                for row in rows:
                    self.print(row)
        elif kind == "tool_start":
            self._flush_markdown()
            self._assistant_open = False
            if self.output_style == "detailed":
                for row in rows:
                    self.print(row)
            self.resume_live()
            call = render_tool_call(data, theme=self.theme, width=self.console.width, api_key=self._api_key)
            if self._live is not None and self._live_running:
                self._live.update(call)
            elif self.output_style == 'detailed' or self._tool_group is None:
                self.print(call)
        elif kind == "tool_end":
            self._flush_markdown()
            merged = tracker.last_tool if tracker is not None else data
            if self.output_style == 'detailed':
                for row in render_tool_rows(merged, width=max(1, self.console.width - 2), theme=self.theme, api_key=self._api_key, output_style=self.output_style):
                    self.print(indent(row))
            else:
                if self._tool_group is None:
                    self._tool_group = ToolGroup(1, self.theme, self._api_key, interactive=False)
                self._tool_group.add(len(self._tool_group.entries) + 1, merged)
                if ToolGroup.failed(merged):
                    from ui.presentation import render_tool_line
                    self.print(indent(render_tool_line(merged, width=max(1, self.console.width - 2),
                        theme=self.theme, api_key=self._api_key)))
            self._assistant_open = False
            if self._live is not None and self._live_running:
                self._live.update(self._thinking_renderable())
        elif kind == "token":
            self._flush_tools()
            self.stream_text(str(data.get("text", "")))
        elif kind == "approval_needed":
            self._flush_markdown()
            self.set_status("waiting_approval")
        elif kind == 'delivery':
            self._delivery_text = str(data.get('text', ''))
        elif kind == "final":
            self._flush_tools()
            if not self._assistant_open:
                self.stream_text(str(data.get("text", "")))
            self.pause_live()
            self._flush_markdown()
            if not self._delivery_text and data.get('delivery'):
                from nailong.core.delivery import render_delivery_report
                self._delivery_text = render_delivery_report(data['delivery'])
            if self._delivery_text:
                self.print(Text(self._delivery_text, style=None if self.theme.no_color else self.theme.muted))
                self._delivery_text = ''
            if self.output_style == "detailed":
                for row in rows:
                    self.print(row)
            self.set_status("ready")
        elif kind == "error":
            self.pause_live()
            self._flush_markdown()
            self._flush_tools()
            if tracker is not None:
                self.print(render_completion(tracker.steps, ok=False, theme=self.theme))
            self.print(Text(str(data.get("message", "请求失败")), style=None if self.theme.no_color else self.theme.danger))
        elif kind == "hook_blocked":
            self._flush_markdown()
            self.error(str(data.get("reason", "项目钩子阻止了操作。")))
        elif kind == "hook_feedback":
            self._flush_markdown()
            text = str(data.get("text", "")).strip()
            if text:
                self.print(Text(f"钩子反馈：{text}", style=None if self.theme.no_color else self.theme.muted))

    def _flush_tools(self):
        if self._tool_group is not None:
            self.print(self._tool_group)
            self._tool_group = None

    def stream_text(self, text: str) -> None:
        if not text:
            return
        if not self._assistant_open:
            self.print(render_role_header("assistant", theme=self.theme))
            self._assistant_open = True
        self._received_chars += len(text)
        for block in self._markdown.feed(text):
            self.print_markdown(block)
        if self._live is not None and self._live_running and self._status == "thinking":
            self._live.update(self._thinking_renderable())

    def error(self, message: str) -> None:
        self.print(Text(message, style=None if self.theme.no_color else self.theme.danger))
