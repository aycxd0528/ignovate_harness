"""Shared, side-effect-free dashboard rows for Textual and inline output."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.cells import cell_len
from rich.padding import Padding
from rich.text import Text

from ui.render import elide_middle
from ui.theme import Theme, load_theme


_DEFAULT_CONTEXT_WINDOWS = {
    "deepseek-flash": 1_000_000,
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-pro": 1_000_000,
    "deepseek-chat": 65_536,
}


def configured_context_window(model: str, project_root: str | Path) -> int | None:
    """Resolve a positive context window from safe project settings or defaults."""
    from nailong.core.context import configured_context_window as resolve_window
    try:
        return resolve_window(model, project_root)
    except (OSError, UnicodeError, ValueError, RuntimeError):
        return _DEFAULT_CONTEXT_WINDOWS.get(model.casefold())


def _redact(value: Any, api_key: str = "") -> str:
    text = str(value if value is not None else "")
    return text.replace(api_key, "[密钥已隐藏]") if api_key else text


def context_percent(usage: dict, window: int | None) -> str | None:
    if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
        return None
    try:
        tokens = max(0, int(usage.get("input_tokens", 0) or 0))
    except (TypeError, ValueError, AttributeError):
        return None
    return f"{tokens / window * 100:.1f}%"


@dataclass(frozen=True)
class StepState:
    step: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    context_window: int | None = None
    usage_known: bool = False


@dataclass
class SessionMetrics:
    """The visible session totals, including the latest model context sample."""

    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    last_input_tokens: int | None = None
    usage_complete: bool = True
    context_history: list[int | None] = field(default_factory=list)

    def begin_context_turn(self) -> None:
        """Reserve one sample per visible user turn, updated by its main calls."""
        self.context_history.append(None)
        del self.context_history[:-8]
        self.last_input_tokens = None

    def _set_context_sample(self, value: int | None) -> None:
        if not self.context_history:
            self.context_history.append(value)
        else:
            self.context_history[-1] = value
        self.last_input_tokens = value

    @classmethod
    def from_events(cls, events: list[dict]) -> "SessionMetrics":
        metrics = cls()
        for record in events:
            if not isinstance(record, dict):
                continue
            if record.get("kind") == "turn_start":
                data = record.get("data") or {}
                if not isinstance(data, dict) or data.get("visible") is not False:
                    metrics.turns += 1
                    metrics.begin_context_turn()
            elif record.get("kind") == "usage":
                metrics.observe_usage(record.get("data") or {})
            elif record.get("kind") == "usage_missing":
                metrics.observe_missing(record.get("data") or {})
        return metrics

    def observe_missing(self, data: dict) -> None:
        self.usage_complete = False
        if data.get('scope') != 'subagent' and not data.get('rewound'):
            self._set_context_sample(None)

    def observe_usage(self, data: dict) -> None:
        def amount(key: str) -> int:
            try:
                return max(0, int(data.get(key, 0) or 0))
            except (TypeError, ValueError, AttributeError):
                return 0

        if data.get('estimated'):
            self.observe_missing(data)
        elif data.get("scope") != "subagent" and not data.get("rewound"):
            value = data.get('input_tokens')
            if type(value) is int and value >= 0:
                self._set_context_sample(value)
            else:
                self.observe_missing(data)
        self.input_tokens += amount("input_tokens")
        self.output_tokens += amount("output_tokens")
        self.cache_hit_tokens += amount("cache_hit_tokens")


def render_model_status(*, model, reasoning_effort=None, permission_mode='default',
                        width=48, theme=None, api_key=''):
    """Render the actual model, effort and permissions for the right side."""
    theme = theme or load_theme()
    model = _redact(model, api_key).replace('\n', ' ')
    from nailong.core.reasoning import REASONING_LABELS, PERMISSION_LABELS
    effort = _redact(REASONING_LABELS.get(reasoning_effort, reasoning_effort or '模型默认'), api_key).replace('\n', ' ')
    permission = PERMISSION_LABELS.get(permission_mode, '帮我批准' if permission_mode=='accept_edits' else '未知权限')
    separator = ' | ' if theme.glyph_running == '>' else ' · '
    suffix = separator.join([effort, permission])
    model_width = width - cell_len(separator + suffix)
    content = (elide_middle(model, model_width) + separator + suffix
               if model_width >= 4 else suffix)
    result = Text(content, style=None if theme.no_color else theme.muted, no_wrap=True, overflow='crop')
    result.truncate(max(0, width), overflow='crop')
    return result


def render_composer_metrics(
    metrics: SessionMetrics,
    *,
    model: str,
    context_window: int | None,
    width: int,
    theme: Theme | None = None,
    api_key: str = "",
    compact: bool = False,
    include_context: bool = True,
) -> Text:
    """Render session totals, optionally including the context occupancy."""
    theme = theme or load_theme()
    model = _redact(model, api_key).replace("\n", " ")
    total = f"{metrics.input_tokens + metrics.output_tokens:,}" + ("（用量不完整）" if not metrics.usage_complete else "")
    if not include_context:
        identity = f'{model}  ·  会话 {metrics.turns} 轮'
        usage = f'Token {total}'
        content = identity + '  ·  ' + usage
        if cell_len(content) > width:
            content = elide_middle(identity, width) + '\n' + elide_middle(usage, width)
        return Text(content, style=None if theme.no_color else theme.muted)
    latest = metrics.last_input_tokens
    context = "—"
    if latest is not None:
        if context_window:
            context = f"{latest:,}/{context_window:,} ({latest / context_window * 100:.1f}%)"
        else:
            context = f"{latest:,}/—"
    if compact:
        context_value = context
        line = (
            f"{model}  ·  会话 {metrics.turns} 轮  ·  "
            f"Token {total}  ·  上下文 {context_value}"
        )
        if cell_len(line) > width and " (" in context_value:
            context_value = context_value.split(" (")[0]
            line = (
                f"{model}  ·  会话 {metrics.turns} 轮  ·  "
                f"Token {total}  ·  上下文 {context_value}"
            )
        if cell_len(line) <= width:
            content = line
        else:
            model_line = f"{elide_middle(model, max(8, width - 12))}  ·  会话 {metrics.turns} 轮"
            context_short = context_value
            if latest is not None and context_window and width < 55:
                window_short = f"{context_window / 1000:.0f}k" if context_window >= 10_000 else str(context_window)
                context_short = f"{latest:,}/{window_short}"
            totals_line = f"Token {total}  ·  上下文 {context_short}"
            if cell_len(totals_line) <= width:
                content = model_line + "\n" + totals_line
            else:
                content = (
                    model_line + "\n" + f"Token {total}" + "\n"
                    + f"上下文 {context_short}"
                )
            content = "\n".join(elide_middle(row, width) for row in content.splitlines())
        return Text(content, style=None if theme.no_color else theme.muted)
    full = (
        f"模型 {model}  ·  会话 {metrics.turns} 轮  ·  "
        f"Token {total}（输入 {metrics.input_tokens:,} / 输出 {metrics.output_tokens:,}"
        f" / 缓存 {metrics.cache_hit_tokens:,}）  ·  上下文 {context}"
    )
    if cell_len(full) <= width:
        content = full
    else:
        model_limit = max(8, min(24, width - 23))
        first = f"模型 {elide_middle(model, model_limit)}  ·  会话 {metrics.turns} 轮"
        context_short = "—"
        context_detail = context
        if latest is not None:
            if context_window:
                window_short = f"{context_window / 1000:.0f}k" if context_window >= 10_000 else str(context_window)
                context_short = f"{latest:,}/{window_short} {latest / context_window * 100:.1f}%"
            else:
                context_short = f"{latest:,}/—"
        second = (
            f"Token {total}  ·  入 {metrics.input_tokens:,} 出 {metrics.output_tokens:,} "
            f"缓存 {metrics.cache_hit_tokens:,}  ·  上下文 {context_detail}"
        )
        if cell_len(second) > width:
            second = (
                f"T{total} 入{metrics.input_tokens:,} 出{metrics.output_tokens:,} "
                f"缓{metrics.cache_hit_tokens:,} · ctx {context_short}"
            )
        if cell_len(second) > width:
            second = (
                f"Token {total} · 入{metrics.input_tokens:,} "
                f"出{metrics.output_tokens:,} 缓{metrics.cache_hit_tokens:,}"
            )
            content = first + "\n" + second + "\n上下文 " + context
        else:
            content = first + "\n" + second
    return Text(content, style=None if theme.no_color else theme.muted)


def render_run_header(
    run_id: str, message: str, *, theme: Theme | None = None, api_key: str = ""
) -> Text:
    theme = theme or load_theme()
    clean_id = _redact(run_id, api_key).replace("\n", " ")
    clean_message = _redact(message, api_key).replace("\n", " ")
    return Text(f"run {clean_id}  {clean_message}", style=None if theme.no_color else theme.accent)


def render_step_header(step: int, *, theme: Theme | None = None) -> Text:
    theme = theme or load_theme()
    return Text(f"模型调用 {step}", style=None if theme.no_color else theme.accent)


def render_step_tokens(state: StepState, *, theme: Theme | None = None) -> Text:
    theme = theme or load_theme()
    if not state.usage_known:
        content = "tokens -"
    else:
        content = (
            f"tokens im={state.input_tokens} out={state.output_tokens} "
            f"cache={state.cache_hit_tokens}"
        )
        percent = context_percent({"input_tokens": state.input_tokens}, state.context_window)
        if percent is not None:
            content += f" ctx:{percent}"
    return Text(content, style=None if theme.no_color else theme.muted)


def indent(renderable: Any, spaces: int = 2):
    """Left-indent any renderable, including Markdown, so bodies align under markers."""
    return Padding(renderable, (0, 0, 0, max(0, int(spaces))))


_ROLE_LABELS = {"user": "你", "assistant": "ignovate harness", "error": "错误", "system": "系统"}


def render_role_header(role: str, *, theme: Theme | None = None) -> Text:
    """Distinct prompt and response markers, including monochrome terminals."""
    theme = theme or load_theme()
    label = _ROLE_LABELS.get(role, role)
    color = theme.role_color(role)
    style = None if theme.no_color else (f"bold {color}" if color else "bold")
    marker = Text()
    ascii_only = theme.glyph_running == ">"
    glyph = (">" if ascii_only else "❯") if role == "user" else ("!" if ascii_only else "✗") if role == "error" else ("*" if ascii_only else "●")
    marker.append(f"{glyph} ", style=style)
    marker.append(label, style=style)
    return marker


def render_tool_group_header(count: int, total_ms: int | None = None, *, theme: Theme | None = None) -> Text:
    """Label the tool rows of one turn; only worth showing once there are several."""
    theme = theme or load_theme()
    text = f"工具 {count} 项"
    if isinstance(total_ms, int) and total_ms >= 0:
        text += f" · {total_ms}ms"
    return Text(text, style=None if theme.no_color else theme.tool_label)


def format_tool_args(
    name: str, preview: dict | None, *, limit: int = 60, api_key: str = ""
) -> str:
    """Show argument values only: no key names, no repr quotes."""
    if not isinstance(preview, dict):
        return ""
    allowed = ("path", "command", "max_depth", "offset", "limit", "pattern", "query")
    parts = []
    for key in allowed:
        if key not in preview:
            continue
        value = preview[key]
        if value is None or isinstance(value, (dict, list)):
            continue
        parts.append(str(_redact(value, api_key)).replace("\n", " "))
    return elide_middle(" · ".join(parts), limit)


_TOOL_LABELS = {
    "list_files": "列出文件", "read_file": "读取", "glob": "查找文件",
    "grep": "搜索", "search_text": "搜索文本", "edit_file": "编辑",
    "write_file": "写入", "run_command": "运行", "task": "子代理",
    "load_skill": "加载 Skill", "read_skill_resource": "读取 Skill 资源",
    "exit_plan_mode": "提交计划", "update_goal": "更新目标",
}


def tool_label(name: str) -> str:
    return _TOOL_LABELS.get(name, name)


def render_tool_line(
    data: dict, *, width: int = 80, theme: Theme | None = None, api_key: str = ""
) -> Text:
    """Fit an action and its outcome, giving exit status priority over long args."""
    theme = theme or load_theme()
    name = str(data.get("name", "tool"))
    label = " ".join(_redact(tool_label(name), api_key).split())
    running = data.get("state") == "running"
    ok = data.get("ok") is not False and not data.get("timed_out") and data.get("exit_code") in (None, 0)
    glyph = theme.glyph_running if running else theme.glyph_ok if ok else theme.glyph_fail
    suffixes = []
    if running:
        suffixes.append("执行中")
        if isinstance(data.get('display_elapsed_seconds'), (int, float)):
            suffixes.append(f'{max(0, data["display_elapsed_seconds"]):.1f}s')
    elif data.get("timed_out"):
        suffixes.append("超时")
    elif not ok:
        suffixes.append("失败")
    if not running and data.get("exit_code") is not None:
        suffixes.append(f"退出码 {data['exit_code']}")
    elapsed = data.get("elapsed_ms")
    if isinstance(elapsed, int) and not running:
        suffixes.append(f"{max(0, elapsed)}ms")
    prefix = f"{glyph} {label}"
    while suffixes and cell_len(prefix + " · " + " · ".join(suffixes)) > width and suffixes[-1].endswith("ms"):
        suffixes.pop()
    suffix = " · " + " · ".join(suffixes) if suffixes else ""
    prefix = elide_middle(prefix, max(0, width - cell_len(suffix)))
    available = max(0, width - cell_len(prefix + suffix) - 1)
    preview = data.get("preview") if isinstance(data.get("preview"), dict) else {}
    if not preview and data.get("path"):
        preview = {"path": data["path"]}
    args = format_tool_args(name, preview, limit=available, api_key=api_key)
    content = prefix + (f" {args}" if args else "") + suffix
    result = Text(elide_middle(_redact(content, api_key), max(0, width)), style=None if theme.no_color else theme.tool_line if ok else theme.danger)
    if not theme.no_color:
        result.stylize(theme.accent if running else theme.success if ok else theme.danger, 0, len(glyph))
    return result


def render_tool_rows(
    data: dict, *, width: int = 80, theme: Theme | None = None,
    api_key: str = "", output_style: str = "normal",
) -> list[Text]:
    """A compact action, bounded output/diff and a readable result underneath."""
    theme = theme or load_theme()
    rows = [render_tool_line(data, width=width, theme=theme, api_key=api_key)]
    branch = "  | " if theme.glyph_running == ">" else "  └ "
    muted = None if theme.no_color else theme.muted
    summary = " ".join(_redact(data.get("summary") or data.get("error") or "", api_key).split())
    failed = data.get("ok") is False or data.get("timed_out") or data.get("exit_code") not in (None, 0)
    if summary and (failed or output_style != "concise"):
        prefix = "失败：" if failed else ""
        rows.append(Text(elide_middle(branch + prefix + summary, width), style=None if theme.no_color else theme.danger if failed else muted))
    if output_style == "concise":
        return rows
    for key, title in (("output_snippet", "输出预览："), ("diff", "变更预览：")):
        value = (data.get("output_preview") or data.get(key)) if key == "output_snippet" and output_style == "detailed" else data.get(key)
        if not value:
            continue
        lines = _redact(value, api_key).splitlines()
        limit = 60 if output_style == "detailed" else 4 if key == "output_snippet" else 6
        rows.append(Text(branch + title, style=muted))
        for line in lines[:limit]:
            color = theme.success if key == "diff" and line.startswith("+") else theme.danger if key == "diff" and line.startswith("-") else muted
            rows.append(Text(elide_middle("    " + line, width), style=None if theme.no_color else color))
        if len(lines) > limit or data.get("output_truncated") and key == "output_snippet":
            notice = "日志已截断；/tools 查看保留的输出" if data.get("output_truncated") and key == "output_snippet" else "更多内容已折叠；/tools 查看详情"
            rows.append(Text(branch + notice, style=muted))
    return rows


def render_tool_details(data: dict, *, theme: Theme | None = None, api_key: str = "") -> Text:
    """Readable safe fields; neither tool arguments nor raw result JSON are dumped."""
    theme = theme or load_theme()
    result = Text()
    name = str(data.get("name", "tool"))
    result.append(_redact(f"{tool_label(name)}  ({name})\n", api_key), style=None if theme.no_color else f"bold {theme.accent}")
    preview = data.get("preview") if isinstance(data.get("preview"), dict) else {}
    failed = data.get("ok") is False or data.get("timed_out") or data.get("exit_code") not in (None, 0)
    status = "超时" if data.get("timed_out") else "失败" if failed else "已完成"
    result.append(f"状态：{status}\n", style=None if theme.no_color else theme.danger if failed else theme.success)
    for label, value in (
        ("文件", data.get("path") or preview.get("path")),
        ("命令", preview.get("command")), ("工作目录", preview.get("cwd")),
        ("退出码", data.get("exit_code")), ("耗时", f"{data['elapsed_ms']}ms" if data.get("elapsed_ms") is not None else None),
        ("结果", data.get("summary")),
    ):
        if value is not None and value != "":
            result.append(_redact(f"{label}：{value}\n", api_key))
    for key, title in (("output_preview", "命令输出"), ("diff", "文件变更")):
        value = data.get(key) or (data.get("output_snippet") if key == "output_preview" else None)
        if not value:
            continue
        result.append(f"\n{title}\n", style=None if theme.no_color else f"bold {theme.accent}")
        for line in _redact(value, api_key).splitlines():
            color = theme.success if key == "diff" and line.startswith("+") else theme.danger if key == "diff" and line.startswith("-") else None
            result.append(line + "\n", style=None if theme.no_color else color)
    if data.get("output_truncated"):
        result.append("\n输出已截断，仅显示保留的日志。\n", style=None if theme.no_color else theme.muted)
    return result


def tool_records(events: list[dict]) -> list[dict]:
    """Pair saved starts/results so restored details retain the command and path."""
    started = {}
    records = []
    allowed = {"name", "path", "preview", "summary", "ok", "exit_code", "timed_out", "elapsed_ms", "output_snippet", "output_preview", "output_truncated", "diff"}
    for event in events:
        data = event.get("data") or {}
        call_id = data.get("call_id", "")
        if event.get("kind") == "tool_start":
            started[call_id] = data
        elif event.get("kind") == "tool_end":
            merged = {**started.pop(call_id, {}), **data}
            if 'elapsed_ms' not in merged and 'duration_ms' in merged:
                merged['elapsed_ms'] = merged['duration_ms']
            records.append({key: value for key, value in merged.items() if key in allowed})
    return records


def render_completion(
    steps: int, *, ok: bool = True, theme: Theme | None = None,
    tool_calls: int | None = None, graph_steps: int | None = None,
) -> Text:
    theme = theme or load_theme()
    glyph = theme.glyph_ok if ok else theme.glyph_fail
    status = "完成" if ok else "已停止"
    text = f"{glyph} {status} · {steps} 次模型调用"
    if tool_calls is not None:
        text += f" · {tool_calls} 次工具调用"
    if graph_steps is not None:
        text += f" · {graph_steps} 个内部步骤"
    return Text(text, style=None if theme.no_color else (theme.success if ok else theme.danger))


def render_welcome(
    *,
    project: str,
    model: str,
    thread_id: str,
    theme: Theme | None = None,
    api_key: str = "",
    width: int = 80,
) -> Text:
    """A quiet three-line prompt for an empty conversation."""
    theme = theme or load_theme()
    no_color = theme.no_color
    muted = None if no_color else theme.muted
    strong = None if no_color else f"bold {theme.accent}"

    card = Text()
    card.append("本地编码助手", style=strong)
    card.append("  ·  ", style=muted)
    card.append(elide_middle(Path(_redact(project, api_key)).name, max(12, width - 24)))
    card.append("\n试试：介绍这个项目 · 检查失败的测试\n", style=muted)
    card.append("常用  ", style=muted)
    for index, name in enumerate(("/help", "/model", "/reasoning", "/permissions")):
        if index:
            card.append("  ·  ", style=muted)
        card.append(name, style=None if no_color else theme.accent)
    card.append("\n任务  /review · /plan · /goal · /sessions", style=muted)
    return card


class StepTracker:
    def __init__(
        self, *, context_window: int | None = None, api_key: str = "",
        theme: Theme | None = None, width: int = 80,
    ) -> None:
        self.context_window = context_window
        self.api_key = api_key
        self.theme = theme or load_theme()
        self.width = width
        self.steps = 0
        self.tool_calls = 0
        self.graph_steps = None
        self._step_source: str | None = None
        self._started_tools: dict[str, dict] = {}
        self.last_tool: dict = {}

    def _fallback_step(self) -> list[Text]:
        self.steps += 1
        self._step_source = "fallback"
        state = StepState(step=self.steps, context_window=self.context_window)
        return [render_step_header(self.steps, theme=self.theme), render_step_tokens(state, theme=self.theme)]

    def observe(self, event: Any) -> list[Text]:
        kind = getattr(event, "kind", "")
        data = getattr(event, "data", {}) or {}
        if kind == "usage":
            if data.get("scope") == "subagent":
                return []
            self.steps += 1
            self._step_source = "usage"
            def amount(key):
                try:
                    return max(0, int(data.get(key, 0) or 0))
                except (TypeError, ValueError):
                    return 0
            state = StepState(
                step=self.steps,
                input_tokens=amount("input_tokens"),
                output_tokens=amount("output_tokens"),
                cache_hit_tokens=amount("cache_hit_tokens"),
                context_window=self.context_window,
                usage_known=True,
            )
            return [render_step_header(self.steps, theme=self.theme), render_step_tokens(state, theme=self.theme)]
        if kind == "tool_start":
            self.tool_calls += 1
            rows = self._fallback_step() if self._step_source is None else []
            call_id = str(data.get("call_id", "") or "")
            self._started_tools[call_id] = data
            return rows
        if kind == "tool_end":
            rows = self._fallback_step() if self._step_source is None else []
            call_id = str(data.get("call_id", "") or "")
            start = self._started_tools.pop(call_id, {})
            merged = {**start, **data}
            self.last_tool = merged
            rows.extend(render_tool_rows(merged, width=self.width, theme=self.theme, api_key=self.api_key))
            if not self._started_tools:
                self._step_source = None
            return rows
        if kind == 'delivery':
            return [Text(_redact(data.get('text', ''), self.api_key), style=self.theme.muted)]
        if kind == "final":
            stats = data.get('stats') or {}
            if stats.get('model_calls', 0) > 0:
                self.steps = stats['model_calls']
            self.tool_calls = stats.get('tool_calls', self.tool_calls)
            self.graph_steps = stats.get('graph_steps')
            return [render_completion(self.steps, theme=self.theme, tool_calls=self.tool_calls,
                                      graph_steps=self.graph_steps)]
        return []
