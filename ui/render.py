"""Pure Rich renderables for events, diffs, and approval prompts."""

from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
from typing import Any

from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.text import Text

from ui.theme import Theme, load_theme


def _data(event: Any) -> dict:
    value = getattr(event, "data", event)
    return value if isinstance(value, dict) else {}


def _plain(value: Any, api_key: str = "") -> str:
    text = str(value if value is not None else "")
    return text.replace(api_key, "[密钥已隐藏]") if api_key else text


def elide_middle(value: str, width: int) -> str:
    """Keep both ends of a long path while respecting terminal cell width."""
    if width <= 0:
        return ""
    if cell_len(value) <= width:
        return value
    if width <= 3:
        return "." * width
    left_width = (width - 3 + 1) // 2
    right_width = width - 3 - left_width
    left: list[str] = []
    used = 0
    for char in value:
        amount = cell_len(char)
        if used + amount > left_width:
            break
        left.append(char)
        used += amount
    right: list[str] = []
    used = 0
    for char in reversed(value):
        amount = cell_len(char)
        if used + amount > right_width:
            break
        right.append(char)
        used += amount
    result = "".join(left) + "..." + "".join(reversed(right))
    while cell_len(result) > width and right:
        right.pop(0)
        result = "".join(left) + "..." + "".join(reversed(right))
    return result


def render_tool_call(event: Any, *, width: int = 80, theme: Theme | None = None, api_key: str = "") -> Text:
    from ui.presentation import render_tool_line
    return render_tool_line({**_data(event), "state": "running"}, width=width, theme=theme, api_key=api_key)


def render_tool_result(event: Any, *, theme: Theme | None = None, api_key: str = "") -> Text:
    data = _data(event)
    theme = theme or load_theme()
    ok = bool(data.get("ok", True))
    glyph = theme.glyph_ok if ok else theme.glyph_fail
    name = _plain(data.get("name", "tool"), api_key)
    summary = _plain(data.get("summary", "完成" if ok else "失败"), api_key)
    elapsed = data.get("elapsed_ms")
    suffix = f" · {elapsed}ms" if isinstance(elapsed, int) else ""
    result = Text(f"{glyph} {name} → {summary}{suffix}")
    if not theme.no_color:
        result.stylize(theme.success if ok else theme.danger, 0, len(glyph))
    return result


def render_diff(
    old: str,
    new: str,
    path: str,
    *,
    context: int = 3,
    width: int = 80,
    theme: Theme | None = None,
) -> Group:
    """Render a readable unified diff without writing or printing anything."""
    theme = theme or load_theme()
    header = Text(elide_middle(path, max(12, width - 10)), style=None if theme.no_color else theme.accent)
    lines = list(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="before",
            tofile="after",
            n=context,
        )
    )
    rendered = []
    for line in lines:
        content = line.rstrip("\n")
        text = Text(content or " ")
        if not theme.no_color:
            if line.startswith("+") and not line.startswith("+++"):
                text.stylize(theme.success)
            elif line.startswith("-") and not line.startswith("---"):
                text.stylize(theme.danger)
            elif line.startswith("@@") or line.startswith("---") or line.startswith("+++"):
                text.stylize(theme.muted)
        rendered.append(text)
    if not rendered:
        rendered.append(Text("（没有内容变化）"))
    return Group(header, *rendered)


def render_diff_text(diff: str, *, theme: Theme | None = None, api_key: str = "") -> Group:
    """Color the +/- lines of an already-produced unified diff string."""
    theme = theme or load_theme()
    rendered = []
    for line in str(diff).splitlines():
        item = Text(_plain(line, api_key))
        if not theme.no_color:
            if line.startswith("+") and not line.startswith("+++"):
                item.stylize(theme.success)
            elif line.startswith("-") and not line.startswith("---"):
                item.stylize(theme.danger)
            elif line.startswith("@@"):
                item.stylize(theme.muted)
        rendered.append(item)
    return Group(*rendered)


def render_output_snippet(snippet: str, *, theme: Theme | None = None, api_key: str = "") -> Group:
    """Render a muted command-output preview under a tool result line."""
    theme = theme or load_theme()
    header = Text("输出预览：", style=None if theme.no_color else theme.muted)
    body = Text(_plain(snippet, api_key))
    if not theme.no_color:
        body.stylize(theme.muted)
    return Group(header, body)


def render_approval(
    action: dict,
    *,
    width: int = 80,
    theme: Theme | None = None,
    api_key: str = "",
) -> Group:
    theme = theme or load_theme()
    name = _plain(action.get("name", "unknown"), api_key)
    args = action.get("args", {}) or {}
    approval = action.get("_approval", {}) or {}
    reason = _plain(approval.get("reason", "此操作需要确认。"), api_key)
    rows: list[Any] = [Text(f"需要批准：{name}", style=None if theme.no_color else theme.accent), Text(reason)]
    preview = approval.get("preview", {}) or {}
    path = _plain(preview.get("path") or args.get("path") or "", api_key)
    if path:
        rows.append(Text(f"文件：{elide_middle(path, max(12, width - 8))}"))
    diff = preview.get("diff")
    if diff:
        rows.append(render_diff_text(str(diff), theme=theme, api_key=api_key))
    elif name == "run_command" and args.get("command"):
        rows.append(Text("命令：" + _plain(args.get("command"), api_key)))
    return Group(*rows)


def render_plan(markdown: str, path: Path, *, theme: Theme | None = None) -> Group:
    theme = theme or load_theme()
    heading = Text(f"计划：{path}", style=None if theme.no_color else theme.accent)
    return Group(heading, Markdown(markdown))


def render_goal(goal: Any, *, theme: Theme | None = None) -> Text:
    theme = theme or load_theme()
    if isinstance(goal, dict):
        label = goal.get("title") or goal.get("name") or goal.get("status") or "目标"
    else:
        label = goal
    return Text(_plain(label), style=None if theme.no_color else theme.accent)


def _build_toolbar(
    *,
    model: str,
    permission_mode: str,
    project: str,
    tokens: int,
    cost_status: str,
    goal_status: str,
    session_id: str,
    width: int,
    cache_hit_tokens: int = 0,
    context_usage_percent: str | None = None,
    session_name: str = "",
) -> str:
    """Compose the bottom status line, dropping low-priority segments to fit.

    Priority order: model > mode > project > tokens/cost > goal > session id.
    """
    segments = [
        model,
        permission_mode,
        elide_middle(project, 24) if project else project,
        f"{tokens} tokens"
        + (f" cache {cache_hit_tokens}" if cache_hit_tokens else "")
        + (f" ctx:{context_usage_percent}" if context_usage_percent else "")
        + cost_status,
    ]
    if goal_status:
        segments.append(goal_status)
    segments.append(f"会话 {elide_middle(session_name, 20)} · {session_id[:8]}" if session_name else f"会话 {session_id[:8]}")
    if width > 0:
        while len(segments) > 1 and cell_len(" · ".join(segments)) > width:
            segments.pop()
    return " · ".join(segments)


def render_user_message(message: str, *, theme: Theme | None = None, api_key: str = "") -> Text:
    """Echo a sent message into the scrollback, bolded, secrets redacted."""
    theme = theme or load_theme()
    label = Text("> " if theme.glyph_running == ">" else "❯ ", style="bold" if theme.no_color else f"bold {theme.role_user or theme.accent}")
    body = Text(_plain(message, api_key))
    if not theme.no_color:
        body.stylize("bold")
    return Text.assemble(label, body)


def render_status(status: Any, *, theme: Theme | None = None) -> Text:
    theme = theme or load_theme()
    if isinstance(status, dict):
        status = status.get("status", "就绪")
    label = _plain(status or "就绪")
    glyph = theme.glyph_running if label in {"thinking", "waiting_approval"} else theme.glyph_ok
    translated = {"thinking": "思考中", "waiting_approval": "等待审批", "ready": "就绪"}.get(label, label)
    text = Text(f"{glyph} {translated}")
    if not theme.no_color:
        text.stylize(theme.accent if label != "waiting_approval" else theme.danger, 0, len(glyph))
    return text
