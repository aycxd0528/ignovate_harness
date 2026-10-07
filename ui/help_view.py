"""Concise terminal help and a local, full-screen command browser.

The screen only returns a command name to its caller; it never executes one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from rich.cells import cell_len
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from ui.interaction import InteractionPanel, ChoiceList, FieldInput
from textual.widgets import Button, Input, OptionList, Static, Tab, Tabs
from textual.widgets.option_list import Option

from ui.commands import COMMAND_BY_NAME, CommandSpec
from ui.theme import Theme, load_theme


_SECTIONS = {
    "general": "常规",
    "commands": "命令",
    "custom": "自定义命令",
    "skills": "Skills",
}
_COMMAND_GROUPS = (
    ("常用与任务", ("/help", "/init", "/plan", "/task", "/goal", "/review", "/diff", "/verify")),
    ("会话与记录", ("/project", "/history", "/sessions", "/resume", "/rewind", "/clear",
                    "/rename", "/recap", "/export", "/count")),
    ("运行与工具", ("/stop", "/queue", "/tools", "/mcp", "/permissions")),
    ("上下文与用量", ("/cost", "/context", "/compact", "/memory")),
    ("设置与诊断", ("/model", "/reasoning", "/theme", "/config", "/doctor", "/status", "/about",
                    "/skills", "/reload-skills", "/exit")),
)
_EMPTY = {
    "commands": "当前没有内置命令。",
    "custom": "当前没有自定义命令。可在 .nailong/commands 中添加。",
    "skills": "当前没有发现本地 Skill。可在 .agents/skills/<名称>/SKILL.md 中添加。",
}


def _redact(value: str, api_key: str) -> str:
    return str(value).replace(api_key, "[密钥已隐藏]") if api_key else str(value)


def _section(value: str) -> str:
    return value if value in _SECTIONS else "general"


def _groups(specs: Iterable[CommandSpec], section: str) -> list[tuple[str, tuple[CommandSpec, ...]]]:
    specs = tuple(specs)
    if section == "skills":
        rows = tuple(spec for spec in specs if spec.name.startswith("$"))
        return [("Skills", rows)] if rows else []
    if section == "custom":
        rows = tuple(spec for spec in specs if not spec.name.startswith("$") and spec.name not in COMMAND_BY_NAME)
        return [("自定义命令", rows)] if rows else []
    if section != "commands":
        return []
    builtins = {spec.name: spec for spec in specs if spec.name in COMMAND_BY_NAME}
    groups = []
    for label, names in _COMMAND_GROUPS:
        rows = tuple(builtins.pop(name) for name in names if name in builtins)
        if rows:
            groups.append((label, rows))
    if builtins:
        groups.append(("其他命令", tuple(builtins.values())))
    return groups


def _brief(description: str) -> str:
    """Keep the first nonempty line and sentence, without losing full details."""
    first = next((line.strip() for line in description.splitlines() if line.strip()), "暂无说明")
    sentence = re.search(r"[。！？!?]|\.(?=\s|$)", first)
    return first[:sentence.end()] if sentence else first


def _clip(value: str, width: int) -> str:
    if cell_len(value) <= width:
        return value
    if width <= 3:
        return "." * max(0, width)
    used = 0
    result = []
    for character in value:
        used += cell_len(character)
        if used > width - 3:
            break
        result.append(character)
    return "".join(result) + "..."


def _name_width(specs: Iterable[CommandSpec], width: int, api_key: str) -> int:
    longest = max((cell_len(_redact(spec.name, api_key)) for spec in specs), default=0)
    return min(max(14, longest), 26, max(8, width // 3))


def _row(spec: CommandSpec, width: int, name_width: int, theme: Theme, api_key: str) -> Text:
    name = _clip(_redact(spec.name, api_key), name_width)
    summary = _clip(_brief(_redact(spec.description, api_key)), max(1, width - name_width - 4))
    text = Text("  ")
    text.append(name, style=None if theme.no_color else f"bold {theme.accent}")
    text.append(" " * (name_width - cell_len(name) + 2) + summary)
    return text


def render_help_text(
    specs: Iterable[CommandSpec], section: str = "general", width: int = 80,
    theme: Theme | None = None, api_key: str = "",
    *, interactive: bool = True,
) -> Text:
    """Render one help section, keeping the default and Skill summaries short."""
    section = _section(section)
    theme = theme or load_theme()
    width = max(12, width)
    result = Text()
    heading_style = None if theme.no_color else f"bold {theme.accent}"
    result.append("ignovate harness 帮助\n", style=heading_style)
    if section == "general":
        result.append(
            "\n输入与快捷键\n"
            "  Enter        发送消息\n"
            "  Ctrl+Enter   换行\n"
            "  Ctrl+C       停止当前任务；空闲时清空输入\n"
        )
        result.append("  Ctrl+O       展开工具记录\n" if interactive else "  /tools       查看工具详情\n")
        result.append(
            "\n常用入口\n"
            "  /命令  使用内置或自定义命令，例如 /plan、/task、/review\n"
            "  @文件  引用项目文件\n"
            "  $Skill 使用本地 Skill：$名称 <任务>\n"
            "\n浏览更多\n"
            "  /help commands  内置命令按用途分类\n"
            "  /help custom    项目自定义命令\n"
            "  /help skills    本地 Skills 的简短说明\n"
        )
        return result
    groups = _groups(specs, section)
    if not groups:
        result.append("\n" + _EMPTY[section])
        return result
    rows = tuple(spec for _, group in groups for spec in group)
    name_width = _name_width(rows, width, api_key)
    for label, group in groups:
        result.append("\n" + label + "\n", style=heading_style)
        for spec in group:
            result.append_text(_row(spec, width, name_width, theme, api_key))
            result.append("\n")
    if section == "skills":
        result.append("\n输入 $名称 <任务> 调用；全屏帮助中选中条目可查看完整说明。")
    elif section == "custom":
        result.append("\n输入 /名称 [参数] 调用；全屏帮助中选中条目可查看完整说明。")
    return result


class HelpScreen(InteractionPanel):
    """Four-tab help with scrollable summaries and optional full descriptions."""

    BINDINGS = [
        Binding("escape", "close", "关闭", priority=True),
        Binding("ctrl+f", "search", "搜索", priority=True),
        Binding("ctrl+enter", "use", "填入命令", priority=True),
    ]
    preferred_height = 20
    DEFAULT_CSS = """
    HelpScreen { layout: vertical; padding: 0 1; }
    HelpScreen #help-title { height: 1; }
    HelpScreen #help-tabs { height: 1; margin-bottom: 0; }
    HelpScreen.ascii #help-tabs { height: 1; }
    HelpScreen.ascii #help-tabs Tab.-active { text-style: bold reverse; }
    HelpScreen #help-search { height: 3; margin-bottom: 0; }
    HelpScreen #help-body { height: 1fr; min-height: 1; }
    HelpScreen #help-general-scroll { height: 1fr; }
    HelpScreen #help-general { height: auto; }
    HelpScreen #help-browser { height: 1fr; }
    HelpScreen #help-options { height: 1fr; min-height: 1; border: none; padding: 0; }
    HelpScreen #help-empty { height: auto; padding: 1 0; }
    HelpScreen #help-detail-scroll { height: 2fr; min-height: 1; border-top: solid $primary; }
    HelpScreen #help-detail { height: auto; }
    HelpScreen #help-use { height: 1; min-width: 16; margin-top: 0; }
    HelpScreen #help-footer { height: 1; margin-top: 0; }
    """

    def __init__(
        self, specs: Iterable[CommandSpec], theme: Theme, api_key: str = "",
        initial_tab: str = "general",
    ) -> None:
        super().__init__()
        self._specs = tuple(specs)
        self._theme = theme
        self._api_key = api_key
        self._section = _section(initial_tab)
        self._option_specs: dict[str, CommandSpec] = {}
        self._selected: CommandSpec | None = None
        self.set_class(theme.glyph_running == ">", "ascii")

    def compose(self) -> ComposeResult:
        yield Static(Text("帮助", style=None if self._theme.no_color else f"bold {self._theme.accent}"), id="help-title")
        yield Tabs(*(Tab(label, id="help-tab-" + section) for section, label in _SECTIONS.items()),
                   active="help-tab-" + self._section, id="help-tabs")
        yield FieldInput(placeholder="搜索名称、用法或描述  (Ctrl+F)", id="help-search")
        with Vertical(id="help-body"):
            with VerticalScroll(id="help-general-scroll"):
                yield Static(render_help_text(self._specs, theme=self._theme, api_key=self._api_key),
                             markup=False, id="help-general")
            with Vertical(id="help-browser"):
                yield ChoiceList(markup=False, compact=True, id="help-options")
                yield Static("", markup=False, id="help-empty")
                with VerticalScroll(id="help-detail-scroll"):
                    yield Static("", markup=False, id="help-detail")
                yield Button("填入输入框", id="help-use", disabled=True)
        yield Static("Ctrl+F 搜索 · Enter 详情 · Ctrl+Enter 填入 · Esc 返回",
                     markup=False, id="help-footer")

    def on_mount(self) -> None:
        self.apply_theme(self._theme)
        self._apply_theme()
        self._refresh_section()
        self.query_one("#help-tabs" if self._section == "general" else "#help-options").focus()

    def _apply_theme(self) -> None:
        theme = self._theme
        foreground = "ansi_default" if theme.no_color else theme.foreground
        background = "ansi_default" if theme.no_color else theme.background
        border = "ascii" if theme.glyph_running == ">" else "solid"
        self.styles.background = background
        self.styles.color = foreground
        for widget in self.query("Vertical, VerticalScroll, OptionList, Input, Button, Tabs"):
            widget.styles.background = background
            widget.styles.color = foreground
        border_color = foreground if theme.no_color else theme.border
        self.query_one("#help-search").styles.border = (border, border_color)
        self.query_one("#help-detail-scroll").styles.border_top = (border, border_color)
        if theme.glyph_running == ">":
            self.query_one("#help-use").styles.border = ("ascii", border_color)
            for widget in self.query("Underline"):
                widget.display = False
            for widget in self.query("OptionList, VerticalScroll"):
                widget.styles.scrollbar_size_vertical = 0
        self.query_one("#help-footer").styles.color = foreground if theme.no_color else theme.muted

    def _reset_detail(self) -> None:
        self._selected = None
        self.query_one("#help-detail", Static).update(Text())
        self.query_one("#help-detail-scroll").display = False
        button = self.query_one("#help-use", Button)
        button.disabled = True
        button.display = False

    def _refresh_section(self) -> None:
        general = self._section == "general"
        self.query_one("#help-general-scroll").display = general
        self.query_one("#help-browser").display = not general
        self.query_one("#help-search").display = not general
        self._reset_detail()
        if not general:
            self._refresh_options()

    def _refresh_options(self, *, preserve_state: bool = False) -> None:
        options = self.query_one("#help-options", OptionList)
        highlighted = self._highlighted_spec() if preserve_state else None
        list_y = options.scroll_y
        details = self.query_one("#help-detail-scroll", VerticalScroll)
        details_y = details.scroll_y
        query = self.query_one("#help-search", Input).value.strip().casefold()
        groups = _groups(self._specs, self._section)
        filtered = []
        for label, group in groups:
            rows = tuple(spec for spec in group if query in _redact(
                " ".join((spec.name, spec.usage, spec.description)), self._api_key).casefold())
            if rows:
                filtered.append((label, rows))
        width = max(12, self.size.width - 6)
        name_width = _name_width((spec for _, group in filtered for spec in group), width, self._api_key)
        content = []
        self._option_specs = {}
        for label, group in filtered:
            heading = Text(label, style=None if self._theme.no_color else f"bold {self._theme.muted}")
            content.append(Option(heading, disabled=True))
            for spec in group:
                identifier = "help-option-" + str(len(self._option_specs))
                self._option_specs[identifier] = spec
                content.append(Option(_row(spec, width, name_width, self._theme, self._api_key), id=identifier))
        first_index = next((index for index, option in enumerate(content) if not option.disabled), None)
        restored_index = next((index for index, option in enumerate(content)
                               if highlighted is not None and option.id in self._option_specs
                               and self._option_specs[option.id].name == highlighted.name), first_index)
        # Rebuilding OptionList resets its highlight and emits transient events.
        # Resize is a reflow, so those events must not clear the selected details.
        with options.prevent(OptionList.OptionHighlighted):
            options.set_options(content)
            options.highlighted = restored_index if preserve_state else first_index
        options.display = bool(content)
        empty = self.query_one("#help-empty", Static)
        empty.display = not content
        empty.update(Text("没有匹配的条目。请缩短关键词或清空搜索。" if query else _EMPTY[self._section]))
        if preserve_state:
            def restore_scroll() -> None:
                options.scroll_to(y=list_y, animate=False, immediate=True)
                details.scroll_to(y=details_y, animate=False, immediate=True)

            # A second Resize may arrive before the refresh callback. Restore
            # now as well so it captures the original position, not the list's
            # automatic scroll-to-highlight during rebuilding.
            restore_scroll()
            self.call_after_refresh(restore_scroll)

    @on(Tabs.TabActivated, "#help-tabs")
    def _tab_changed(self, event: Tabs.TabActivated) -> None:
        self._section = _section((event.tab.id or "").removeprefix("help-tab-"))
        self._refresh_section()

    @on(Input.Changed, "#help-search")
    def _search_changed(self, event: Input.Changed) -> None:
        safe_query = _redact(event.value, self._api_key)
        if safe_query != event.value:
            event.input.value = safe_query
        if self._section != "general":
            self._reset_detail()
            self._refresh_options()

    @on(Input.Submitted, "#help-search")
    def _search_submitted(self) -> None:
        options = self.query_one("#help-options", OptionList)
        options.focus()
        self._show_highlighted()

    @on(OptionList.OptionSelected, "#help-options")
    def _option_selected(self, event: OptionList.OptionSelected) -> None:
        if spec := self._option_specs.get(event.option_id or ""):
            self._show_detail(spec)

    @on(OptionList.OptionHighlighted, "#help-options")
    def _option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if self._selected is not None and self._option_specs.get(event.option_id or "") != self._selected:
            self._reset_detail()

    @on(Button.Pressed, "#help-use")
    def _use_pressed(self) -> None:
        self.action_use()

    def _highlighted_spec(self) -> CommandSpec | None:
        options = self.query_one("#help-options", OptionList)
        if options.highlighted is None:
            return None
        option = options.get_option_at_index(options.highlighted)
        return self._option_specs.get(option.id or "")

    def _show_highlighted(self) -> None:
        if spec := self._highlighted_spec():
            self._show_detail(spec)

    def _show_detail(self, spec: CommandSpec) -> None:
        self._selected = spec
        usage = spec.usage or (spec.name + " <任务>" if spec.name.startswith("$") else
                               spec.name + " <参数>" if spec.needs_argument else spec.name)
        detail = Text()
        detail.append(_redact(usage, self._api_key),
                      style=None if self._theme.no_color else f"bold {self._theme.accent}")
        detail.append("\n\n" + _redact(spec.description or "暂无说明", self._api_key))
        self.query_one("#help-detail", Static).update(detail)
        scroll = self.query_one("#help-detail-scroll", VerticalScroll)
        scroll.display = True
        scroll.scroll_home(animate=False)
        button = self.query_one("#help-use", Button)
        button.disabled = False
        button.display = True

    def on_resize(self) -> None:
        if self.is_mounted and self._section != "general":
            self._refresh_options(preserve_state=True)

    def action_search(self) -> None:
        if self._section == "general":
            self.query_one("#help-tabs", Tabs).active = "help-tab-commands"
        self.query_one("#help-search", Input).focus()

    def action_use(self) -> None:
        if self._section != "general" and (spec := self._highlighted_spec()):
            self.dismiss(_redact(spec.name, self._api_key))

    def action_close(self) -> None:
        self.dismiss(None)
