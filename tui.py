"""Full-screen Textual interface for the local LangChain Agent."""

import json
import asyncio
import inspect
import shutil
import sys
import uuid
from time import monotonic
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markdown import Markdown
from rich.console import Group
from rich.text import Text
from rich.style import Style
from rich.cells import cell_len
from textual import on, work, events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from ui.interaction import InteractionPanel
from ui.content_panels import CopyReplyScreen, PlanReviewScreen, PlanEditScreen, RewindConfirmationScreen
from textual.widgets import Button, RichLog, Static, TextArea

from agent import AgentRuntimeFactory
from agent_service import AgentService, TurnRecursionLimitError
from config import ConfigurationError, Settings, select_project_root
from langgraph.errors import GraphRecursionError
import local_tools
from nailong.core.costs import CostEstimator
from nailong.core.permissions import PermissionEngine
from ui.banner import banner_renderable, configured_banner_enabled, configured_reduced_motion
from ui.commands import COMMANDS, COMMAND_BY_NAME, CommandSpec
from ui.controller import CommandController
from ui.actions import CommandActions, IMMEDIATE_COMMANDS, IDENTITY_COMMANDS
from nailong.core.runner import SessionRunner
from ui.flows import drive_goal, run_plan_flow
from ui.presentation import (
    SessionMetrics,
    StepTracker,
    configured_context_window,
    indent,
    render_model_status,
    render_completion,
    render_role_header,
    render_tool_group_header,
    render_tool_line,
    render_tool_rows,
    tool_records,
    render_welcome,
)
from ui.render import elide_middle, render_user_message
from ui.theme import load_theme
from ui.transcript import AssistantTurn, TranscriptLog
from ui.token_weather import render_token_weather
from ui.activity import ToolGroup
from ui.approval_view import ApprovalPrompt, render_approval_content, session_rule
from ui.help_view import HelpScreen

if TYPE_CHECKING:
    from collections.abc import Awaitable


def _is_tool_call_payload(content: str) -> bool:
    """Hide checkpoint-only tool calls from the conversation history."""
    if not isinstance(content, str) or not content.lstrip().startswith("["):
        return False
    try:
        payload = json.loads(content)
    except ValueError:
        return False
    return bool(payload) and isinstance(payload, list) and all(
        isinstance(item, dict)
        and item.get("type") == "tool_call"
        and isinstance(item.get("name"), str)
        for item in payload
    )


class ChatInput(TextArea):
    """Multi-line composer with command-menu-aware key bindings."""

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class MoveSuggestion(Message):
        def __init__(self, delta: int) -> None:
            super().__init__()
            self.delta = delta

    class ChooseSuggestion(Message):
        pass

    class DismissSuggestions(Message):
        pass

    BINDINGS = [
        Binding("enter", "submit_message", show=False, priority=True),
        Binding("ctrl+enter", "insert_newline", show=False, priority=True),
        Binding("up", "move_up", show=False, priority=True),
        Binding("down", "move_down", show=False, priority=True),
        Binding("tab", "choose_suggestion", show=False, priority=True),
        Binding("escape", "dismiss_suggestions", show=False, priority=True),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(
            tab_behavior="indent",
            placeholder="",
            **kwargs,
        )
        self.suggestions_active = False
        self.suggestions_dismissed = False

    def action_submit_message(self) -> None:
        if self.suggestions_active:
            self.post_message(self.ChooseSuggestion())
        else:
            self.post_message(self.Submitted(self.text))

    def action_insert_newline(self) -> None:
        self.insert("\n")

    def action_move_up(self) -> None:
        if self.suggestions_active:
            self.post_message(self.MoveSuggestion(-1))
        else:
            self.action_cursor_up()

    def action_move_down(self) -> None:
        if self.suggestions_active:
            self.post_message(self.MoveSuggestion(1))
        else:
            self.action_cursor_down()

    def action_choose_suggestion(self) -> None:
        if self.suggestions_active:
            self.post_message(self.ChooseSuggestion())
        else:
            self.insert("    ")

    def action_dismiss_suggestions(self) -> None:
        if self.suggestions_active:
            self.suggestions_dismissed = True
        self.post_message(self.DismissSuggestions())


class TerminalAgentApp(App[None]):
    """Textual app that keeps all model and tool execution in AgentService."""

    TITLE = "ignovate harness"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen {
        layout: vertical;
        background: #0e1419;
        color: #d9e0e3;
    }
    Screen > .screen--selection { background: #245c83; color: #ffffff; }
    #banner {
        height: 1;
        margin: 0 2;
    }
    #topbar {
        height: 1;
        padding: 0 2;
        background: #151e24;
    }
    #brand, #session-info, #status { width: auto; }
    #brand { color: #9db9bf; text-style: bold; }
    #session-info { width: 1fr; color: #788d95; }
    #status { color: #9eadae; margin-left: 1; }
    #hint {
        display: none;
    }
    #transcript {
        height: 1fr;
        margin: 1 2 0 2;
        padding: 0 1;
        border: none;
        background: #0e1419;
        scrollbar-size-vertical: 1;
        scrollbar-color: #35464f;
        scrollbar-background: #0e1419;
    }
    #transcript.active { margin-top: 0; }
    #tool-activity {
        height: auto;
        max-height: 5;
        margin: 0 2;
        padding: 0 1;
        border-left: solid #526577;
    }
    #command-menu {
        display: none;
        height: auto;
        max-height: 12;
        margin: 0 2;
        padding: 0 2;
        border-top: solid #35464f;
        background: #131d23;
    }
    #interaction-host { display: none; margin: 0 2; height: 12; }
    #composer-frame {
        height: auto;
        min-height: 3;
        max-height: 11;
        margin: 0 2;
        padding: 0 1;
        border: round #536773;
        background: #151f26;
    }
    .panel-open #composer-frame { height: 3; min-height: 3; max-height: 3; }
    .panel-open #composer { height: 1; min-height: 1; max-height: 1; }
    .panel-open #composer-placeholder { display: none; }
    #composer-frame:focus-within { border: round #88c0d0; }
    #composer-placeholder {
        height: 1;
        color: #8699a1;
        text-style: italic;
    }
    #composer {
        height: auto;
        min-height: 1;
        max-height: 7;
        border: none;
        background: #151f26;
        color: #e6ebed;
    }
    #composer-info {
        height: 1;
        margin: 0 2;
        padding: 0 1;
    }
    #composer-stats {
        height: 1;
        width: auto;
        margin-left: 2;
        content-align: right middle;
        color: #81959e;
    }
    #topbar.compact { height: 1; }
    #session-info.compact { display: none; }
    #transcript.compact { margin: 0; padding: 0 1; }
    #composer-frame.compact { margin: 0; border: none; }
    #composer-info.compact { margin: 0; padding: 0 1; }
    #token-weather { height: 1; width: 1fr; }
    #tool-activity.compact { margin: 0; max-height: 3; }
    #hint.compact { display: none; }
    """
    BINDINGS = [Binding("ctrl+c", "stop_current", "Stop", show=False, priority=True),
                Binding('escape', 'clear_body_selection', show=False, priority=True),
                Binding("ctrl+o", "toggle_tools", show=False, priority=True),
                Binding('f6', 'copy_reply', show=False, priority=True),
                Binding('shift+f6', 'copy_previous_reply', show=False, priority=True),
                Binding('f7', 'select_reply', show=False, priority=True),
                Binding('f8', 'session_info', show=False, priority=True)]

    def __init__(
        self,
        service: AgentService,
        settings: Settings,
        *,
        thread_id: str | None = None,
        permission_mode: str = "default",
    ) -> None:
        super().__init__()
        self.service = service
        self.service.ui_name='textual'
        settings = getattr(getattr(service,"runtime_factory",None),"settings",None) or settings
        self.settings = settings
        self.permission_mode = permission_mode
        self.controller = CommandController(settings)
        self.runtime_factories = []
        runtime_factory = getattr(service, "runtime_factory", None)
        self.controller.skill_registry=getattr(runtime_factory,'skill_registry',None)
        if runtime_factory is not None and inspect.iscoroutinefunction(
            getattr(runtime_factory, "aclose", None)
        ):
            self.runtime_factories.append(runtime_factory)
        self.thread_id = thread_id or uuid.uuid4().hex
        self.actions = CommandActions(self.controller)
        if getattr(runtime_factory,'preferences',None) is not None:
            self.actions.preferences=runtime_factory.preferences
        self.session_runner = SessionRunner(settings.project_root, self.thread_id)
        self.completed_turns = 0
        self._filtered_commands: list[CommandSpec] = []
        self._selected_command = 0
        self._selected_command_text: str | None = None
        self._status = "ready"
        self._status_frame = 0
        self._status_timer = None
        self._status_started_at = None
        self._tool_started_at = {}
        self._status_widget = None
        preferences = self.actions.preferences.effective()
        self._ui_theme = load_theme(name=preferences['theme'])
        self._reduced_motion = configured_reduced_motion(settings.project_root)
        self._compact = False
        self._menu_visible = False
        self._banner_available = False
        self._session_metrics = SessionMetrics()
        self._active_tools: dict[str, dict] = {}
        self._turn_tool_count = 0
        self._tool_details=[]
        self._tool_groups = []
        self._current_tool_group = None
        self._tools_expanded = False
        self._pending_tool_ms = 0
        self._turns_written = 0
        self._welcome_visible = False
        self._conversation_started = False
        self._approval_future = None
        self._live_response = None
        self._interaction_panel = None
        self._interaction_future = None
        self._interaction_closed = asyncio.Event()
        self._interaction_closed.set()
        self._approval_closed = asyncio.Event()
        self._approval_closed.set()

    def compose(self) -> ComposeResult:
        project = self._redact(Path(self.settings.project_root).name or str(self.settings.project_root))
        yield Static("", id="banner")
        with Horizontal(id="topbar"):
            yield Static("ignovate harness", id="brand")
            yield Static(
                f"  /  {elide_middle(project, 24)}  ·  {self.thread_id[:8]}  ",
                id="session-info",
            )
            yield Static("● 就绪", id="status")
        yield Static("输入消息开始对话 · 输入 / 触发命令 · Ctrl+C 停止 · /exit 退出", id="hint")
        yield TranscriptLog(id="transcript", min_width=1, markup=False, wrap=True, highlight=False)
        activity = Static("", id="tool-activity")
        activity.display = False
        yield activity
        yield Static(id="command-menu")
        approval = ApprovalPrompt(id='approval-panel')
        approval.display = False
        yield approval
        yield Vertical(id='interaction-host')
        with Horizontal(id='composer-info'):
            yield Static('', id='token-weather', markup=False)
            yield Static('', id='composer-stats', markup=False)
        with Vertical(id="composer-frame"):
            yield Static("输入消息…   Enter 发送 · Ctrl+Enter 换行", id="composer-placeholder")
            yield ChatInput(id="composer")

    def on_mount(self) -> None:
        self._status_widget = self.query_one('#status', Static)
        self.set_focus(self.query_one("#composer", ChatInput))
        self.query_one("#transcript").border_title = " 对话 "
        self.query_one("#composer-frame").border_title = " ❯ 输入 "
        self.query_one("#command-menu").border_title = " 命令 "
        self._apply_layout(self.size.width, self.size.height)
        self._reload_session_metrics()
        self._refresh_preferences()
        if self._session_metrics.turns:
            self._show_history(replace=True)
        else:
            self._show_welcome()
        self._set_status("ready")

    def _refresh_preferences(self):
        previous_theme = self._ui_theme
        self.permission_mode=getattr(self.service, 'permission_mode', self.permission_mode)
        factory=getattr(self.service,'runtime_factory',None)
        self.settings=getattr(factory,'settings',None) or self.settings
        prefs=self.actions.preferences.effective()
        self._ui_theme=load_theme(name=prefs['theme'])
        theme=self._ui_theme
        from ui.theme import semantic_rich_theme
        if getattr(self,'_semantic_theme_pushed',False): self.console.pop_theme()
        self.console.push_theme(semantic_rich_theme(theme));self._semantic_theme_pushed=True
        self.theme='textual-light' if theme.name=='light' else 'textual-dark'
        self.screen.styles.background=theme.background
        self.screen.styles.color=theme.foreground
        muted=theme.muted
        for identifier in ('transcript','tool-activity','composer','composer-frame','topbar','command-menu'):
            widget=self.query_one('#'+identifier)
            widget.styles.background=theme.background
            widget.styles.color=theme.foreground
            widget.styles.scrollbar_background=theme.background
            widget.styles.scrollbar_color=muted
        for identifier in ('composer', 'composer-frame', 'command-menu'):
            self.query_one('#' + identifier).styles.background = theme.surface
        self.query_one('#composer-frame').styles.border = ('round', theme.border)
        self.query_one('#composer-frame').border_title = " > 输入 " if theme.glyph_running == ">" else " ❯ 输入 "
        self.query_one('#composer-placeholder').styles.color = muted
        self.query_one('#composer-stats').styles.color=muted
        self.query_one('#session-info').styles.color=muted
        self.query_one('#brand').styles.color = theme.accent
        if previous_theme != theme:
            self.query_one('#transcript', TranscriptLog).apply_theme(previous_theme, theme)
            self._set_status(self._status)
            self._update_tool_activity()
        self._update_session_info()
        self._update_composer_metrics()

    @work(group='control',exclusive=False)
    async def action_stop_current(self):
        if isinstance(self._interaction_panel, CopyReplyScreen):
            await self._interaction_panel.action_copy_selected()
            return
        selected = self.screen.get_selected_text()
        if not selected and isinstance(self.focused, TextArea):
            selected = self.focused.selected_text
        if selected:
            native = await self._copy_text(selected)
            self.notify('已复制选中文字。' if native else '已发送选中文字的复制请求。')
            return
        if self.screen.selections:
            return
        if self.session_runner.current_item or self.session_runner.busy:
            await self.session_runner.stop()
            from ui.actions import pause_session_goal
            pause_session_goal(self.service,self.thread_id)
            if self._interaction_panel is not None:
                self._interaction_panel.complete(None)
            self._clear_live_response()
            self._clear_tool_activity()
            self._append_system('当前任务已停止，会话保留。待执行输入已暂停；用 /queue resume 继续。')
            self._set_status('paused')
        else:
            self.query_one('#composer',ChatInput).text=''
            self._append_system('输入已清空。用 /exit 退出。')

    def check_action(self, action, parameters):
        if action == 'clear_body_selection':
            return self._interaction_panel is None and bool(self.screen.selections)
        return super().check_action(action, parameters)

    def action_clear_body_selection(self):
        self.screen.clear_selection()

    async def _copy_text(self, text: str) -> bool:
        """Use Textual's OSC52 and macOS's native clipboard when available.

        OSC52 has no acknowledgment. True only confirms a native pbcopy exit.
        Payloads travel via stdin, never shell arguments or command text.
        """
        self.copy_to_clipboard(text)
        executable = shutil.which('pbcopy') if sys.platform == 'darwin' else None
        if not executable:
            return False
        process = None
        try:
            process = await asyncio.create_subprocess_exec(executable,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(process.communicate(text.encode('utf-8')), timeout=2)
            return process.returncode == 0
        except (OSError, asyncio.TimeoutError):
            return False
        finally:
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()

    def _copyable_replies(self) -> list[str]:
        return self.query_one('#transcript', TranscriptLog).reply_texts()

    @work(group='clipboard', exclusive=False)
    async def action_copy_reply(self, previous: bool = False) -> None:
        if isinstance(self._interaction_panel, CopyReplyScreen):
            if previous:
                if self._interaction_panel._index <= 0:
                    self.notify('暂无上一条模型回复。')
                    return
                self._interaction_panel.action_previous()
            await self._interaction_panel.action_copy_all()
            return
        replies = self._copyable_replies()
        offset = 2 if previous else 1
        if len(replies) < offset:
            self.notify('暂无上一条模型回复。' if previous else '暂无可复制的模型回复。')
            return
        native = await self._copy_text(replies[-offset])
        self.notify('已复制上一条回复。' if previous and native else '已复制当前回复。' if native
                    else '已发送复制请求；如未生效，按 F7 选择正文。')

    def action_copy_previous_reply(self) -> None:
        self.action_copy_reply(previous=True)

    @work(group='reading', exclusive=True)
    async def action_select_reply(self) -> None:
        if isinstance(self._interaction_panel, CopyReplyScreen):
            self._interaction_panel.query_one('#copy-body', TextArea).focus()
        elif self._interaction_panel is not None or self._approval_future is not None:
            return
        elif self._copyable_replies():
            await self._wait_panel(CopyReplyScreen(self._copyable_replies, self._ui_theme))
        else:
            self.notify('暂无可选择的模型回复。')

    async def _perform_request(self,request):
        if self.actions.handles(request):
            from ui.model_view import ModelScreen
            from ui.settings_view import SettingScreen
            result=await self.actions.execute(request,self.service,self.thread_id,
                approval=self._request_approval,edit=lambda text:self._wait_panel(PlanEditScreen(self._redact(text),
                    title=('编辑 '+request.argument.split()[-1]+' 记忆；保存后仍需审批') if request.command=='/memory' else '编辑计划草稿；保存后仍需确认执行')),
                model_dialog=lambda prefs,add_only=False,commit=None:self._wait_panel(ModelScreen(prefs,add_only=add_only,
                    global_scope='--global' in request.argument.split()), apply=commit),
                setting_dialog=lambda title,current,options:self._wait_panel(SettingScreen(title,current,options)),
                runner=self.session_runner,emit=lambda event:self._append_system(str(event.data.get('name',''))+' · '+('通过' if event.data.get('ok') else '未通过')))
            if result.text:
                if request.command=="/diff":
                    from ui.render import render_diff_text
                    self.query_one("#transcript",RichLog).write(render_diff_text(result.text,theme=self._ui_theme,api_key=self.settings.api_key))
                else: self._append_system(self._redact(result.text))
            if result.refresh: self._refresh_preferences()
            for model_request in result.model_requests:
                await self._perform_request(model_request)
            return
        if request.command=='/goal':
            factory=getattr(self.service,'runtime_factory',None)
            goal,messages=self.controller.goal(request.argument,factory,self.thread_id)
            for message in messages: self._append_system(message)
            if goal:
                self._append_user(request.message)
                await self._run_goal(goal,self.controller.cost_estimator,request.message)
            return
        if request.command=='/compact':
            await self._compact_session(self.thread_id)
            return
        if request.command=='/rewind':
            await self._rewind_session(self.thread_id)
            return
        self._append_user(request.message)
        if request.profile=='plan':
            value=self._run_plan(request.prompt,request.allowed_tools,request.message)
        else:
            value=self._run_turn(request.prompt,{'configurable':{'thread_id':self.thread_id},'recursion_limit':40},
                request.profile,request.target_path,request.allowed_tools,request.message,request.review_paths)
        if inspect.isawaitable(value): await value

    def _enqueue_request(self,request):
        try:
            pending=self.session_runner.busy or self.session_runner.state=='paused'
            future=self.session_runner.submit(request.message,lambda:self._perform_request(request))
            if pending: self._append_system(f'已加入队列 · {len(self.session_runner.queue)} 项')
            self._await_request(future)
            return True
        except Exception as error:
            self._append_system(self._friendly_error(error))
            return False

    @work(group='queue',exclusive=False)
    async def _await_request(self,future):
        try: await future
        except asyncio.CancelledError: pass
        except Exception as error: self._append_system(self._friendly_error(error))

    @work(group='control',exclusive=False)
    async def _immediate_request(self,request):
        try: await self._perform_request(request)
        except Exception as error: self._append_system(self._friendly_error(error))

    def _show_welcome(self) -> None:
        self._refresh_preferences()
        """Anchor the empty dashboard with identity and the commands that matter."""
        self._welcome_visible = True
        self.query_one("#transcript", RichLog).write(
            render_welcome(
                project=str(self.settings.project_root),
                model=self.settings.model,
                thread_id=self.thread_id,
                theme=self._ui_theme,
                api_key=self.settings.api_key,
                width=self.size.width,
            )
        )

    async def on_unmount(self) -> None:
        self._stop_status_timer()
        self._status_widget = None
        await self.session_runner.close()
        for factory in self.runtime_factories:
            await factory.aclose()
            factory.close()

    def on_resize(self, event) -> None:
        self._size_interaction()
        self._apply_layout(event.size.width, event.size.height)

    def _apply_layout(self, width: int, height: int) -> None:
        project = self._redact(Path(self.settings.project_root).name or str(self.settings.project_root))
        project_width = max(8, min(24, width - 34))
        store=getattr(self.service,'session_store',None)
        list_sessions=getattr(store,'list_sessions',lambda:[])
        name=next((row.get('name','') for row in list_sessions() if row['thread_id']==self.thread_id),'')
        label=elide_middle(self._redact(name),max(8,width-project_width-28))+' · ' if name else ''
        self.query_one("#session-info", Static).update(
            f"  /  {elide_middle(project, project_width)}  ·  {label}{self.thread_id[:8]}  "
        )
        banner = banner_renderable(
            width=width,
            height=height,
            theme=self._ui_theme,
            enabled=configured_banner_enabled(self.settings.project_root),
        )
        if banner is not None:
            banner = Text(
                "ignovate harness",
                style=None if self._ui_theme.no_color else f"bold {self._ui_theme.accent}",
            )
        banner_widget = self.query_one("#banner", Static)
        banner_widget.update(banner or "")
        self._banner_available = banner is not None
        banner_widget.display = self._banner_available and not self._menu_visible and not self._conversation_started
        compact = width < 72 or height < 24
        self._compact = compact
        self.query_one("#hint", Static).display = False
        for selector in (
            "#topbar",
            "#session-info",
            "#transcript",
            "#tool-activity",
            "#composer-frame",
            "#composer-info",
            "#composer-stats",
            "#token-weather",
        ):
            self.query_one(selector).set_class(compact, "compact")
        self.query_one("#transcript").set_class(self._conversation_started, "active")
        self._show_menu(self._menu_visible)
        self._update_composer_metrics(width=width)
        self._update_tool_activity()
        if self._menu_visible:
            self._update_suggestions(self.query_one("#composer", ChatInput).text)

    def _show_menu(self, visible: bool) -> None:
        """Show the command menu and its key hints together, never on their own."""
        self._menu_visible = visible
        menu = self.query_one("#command-menu", Static)
        menu.display = visible
        menu.styles.max_height = 6 if self._compact else 8 if self.size.height < 28 else 12
        self.query_one("#banner", Static).display = self._banner_available and not visible and not self._conversation_started
        show_brand = visible or not self._banner_available or self._conversation_started
        self.query_one("#brand", Static).display = show_brand
        self.query_one("#hint", Static).display = False

    def _redact(self, text: str) -> str:
        key = getattr(self.settings, "api_key", "")
        return text.replace(key, "[密钥已隐藏]") if key else text

    def _reload_session_metrics(self) -> None:
        store = getattr(self.service, "session_store", None)
        try:
            records = store.read_events(self.thread_id) if store is not None else []
            self._session_metrics = SessionMetrics.from_events(records)
            self._tool_details = tool_records(records)
        except (OSError, ValueError, AttributeError):
            self._session_metrics = SessionMetrics()
            self._tool_details=[]
        self._update_composer_metrics()

    def _update_composer_metrics(self, *, width: int | None = None) -> None:
        if not self.is_mounted:
            return
        # Resize events arrive before App.size reflects the new terminal width.
        width = self.size.width if width is None else width
        available = max(1, width - (2 if self._compact else 6))
        window = configured_context_window(self.settings.model, self.settings.project_root)
        factory = getattr(self.service, 'runtime_factory', None)
        model = getattr(factory, 'model', None)
        effort = getattr(model, 'reasoning_effort', None)
        kwargs = getattr(model, 'model_kwargs', {})
        if effort is None and isinstance(kwargs, dict):
            effort = kwargs.get('reasoning_effort')
        if effort is None:
            effort = self.settings.reasoning_effort
        permission = getattr(self.service, 'permission_mode', self.permission_mode)
        status = render_model_status(model=self.settings.model,
            reasoning_effort=effort, permission_mode=permission, width=min(48, max(1, available // 2)),
            theme=self._ui_theme, api_key=self.settings.api_key)
        stats = self.query_one('#composer-stats', Static)
        stats.update(status)
        stats.styles.width = cell_len(status.plain)
        incomplete = '（用量不完整）' if not self._session_metrics.usage_complete else ''
        total = self._session_metrics.input_tokens + self._session_metrics.output_tokens
        stats.tooltip = None
        weather_widget = self.query_one('#token-weather', Static)
        weather_widget.display = True
        weather_widget.update(render_token_weather(self._session_metrics, context_window=window,
            width=max(0, available - cell_len(status.plain) - 2), theme=self._ui_theme, show_empty=True))
        weather_widget.tooltip = None

    def _status_details(self):
        from nailong.core.reasoning import REASONING_LABELS, PERMISSION_LABELS
        effort = self.settings.reasoning_effort
        permission = getattr(self.service, 'permission_mode', self.permission_mode)
        total = self._session_metrics.input_tokens + self._session_metrics.output_tokens
        weather = render_token_weather(self._session_metrics,
            context_window=configured_context_window(self.settings.model, self.settings.project_root),
            width=240, theme=self._ui_theme, show_empty=True).plain
        return self._redact(f'模型：{self.settings.model} · 推理：{REASONING_LABELS.get(effort,effort)} · 权限：{PERMISSION_LABELS.get(permission,permission)}\n'
            f'会话 {self._session_metrics.turns} 轮 · Token {total:,}'+('（用量不完整）' if not self._session_metrics.usage_complete else '')+'\n'+weather)

    def action_session_info(self):
        self._append_system(self._status_details())

    def notify(self, message, *, title='', severity='information', timeout=None, markup=True):
        # Feedback shares the transcript; no transient overlay covers user content.
        if self.is_mounted and self.query('#transcript'):
            self._append_system((title+' · ' if title else '')+str(message))

    def _observe_usage(self, event) -> None:
        if event.kind == "usage":
            self._session_metrics.observe_usage(event.data or {})
            self._update_composer_metrics()
        elif event.kind == 'usage_missing':
            self._session_metrics.observe_missing(event.data or {})
            self._update_composer_metrics()

    def _append_activity(self, event, rows: list[Text]) -> None:
        """Update one retained activity group; detailed mode lists every step."""
        data = getattr(event, "data", {}) or {}
        call_id = str(data.get('call_id', '') or '')
        style = self.actions.preferences.effective()['output_style']
        if style == 'detailed' and event.kind in {'usage', 'tool_start', 'final'}:
            self._flush_tool_rows()
            for row in rows:
                self._write_activity(row)
        if event.kind == 'tool_start':
            self._active_tools[call_id] = data
            self._tool_started_at.setdefault(call_id, monotonic())
            self._update_tool_activity()
            self._set_status('running')
            return
        if event.kind != 'tool_end':
            return
        merged = {**self._active_tools.pop(call_id, {}), **data}
        self._tool_started_at.pop(call_id, None)
        safe={key:merged[key] for key in ('name','path','preview','summary','ok','exit_code','timed_out','elapsed_ms','output_snippet','output_preview','output_truncated','diff') if key in merged}
        self._tool_details.append(safe)
        log = self.query_one('#transcript', TranscriptLog)
        if style == 'detailed':
            for row in render_tool_rows(safe, width=max(1, log.content_size.width - 2), theme=self._ui_theme, api_key=self.settings.api_key, output_style=style):
                row.stylize(Style(meta={'nailong_tool':len(self._tool_details)}))
                self._write_activity(indent(row))
        else:
            if self._current_tool_group is None:
                self._current_tool_group = ToolGroup(len(self._tool_groups) + 1, self._ui_theme, self.settings.api_key)
                self._current_tool_group.expanded = self._tools_expanded
                self._tool_groups.append(self._current_tool_group)
                self._current_tool_group.add(len(self._tool_details), safe)
                self._write_activity(self._current_tool_group)
            else:
                self._current_tool_group.add(len(self._tool_details), safe)
                log.refresh_renderable(self._current_tool_group)
        elapsed = data.get("elapsed_ms")
        if isinstance(elapsed, int) and elapsed >= 0:
            self._pending_tool_ms += elapsed
        self._turn_tool_count += 1
        self._update_tool_activity()
        self._set_status('running' if self._active_tools else 'thinking')

    def _update_tool_activity(self, *, frame=None, now=None) -> None:
        if not self.is_mounted:
            return
        activity = self.query_one('#tool-activity', Static)
        pending = list(self._active_tools.items())
        rows = Text()
        visible = pending[:1]
        now = monotonic() if now is None else now
        frame = self._spinner_frame() if frame is None else frame
        for index, (call_id, data) in enumerate(visible):
            if index:
                rows.append('\n')
            elapsed = max(0, now - self._tool_started_at.get(call_id, now))
            rows.append_text(render_tool_line({**data, 'state': 'running', 'display_elapsed_seconds': elapsed},
                width=max(1, self.size.width - (4 if self._compact else 8)),
                theme=replace(self._ui_theme, glyph_running=frame), api_key=self.settings.api_key))
        if len(pending) > len(visible):
            rows.append(f'\n另有 {len(pending) - len(visible)} 项工具执行中')
        if self._live_response is not None:
            reply = self._live_response
            reply.pending_activity = rows if pending else None
            if not reply.text:
                reply.progress = '' if pending else '正在整理结果…'
            self.query_one('#transcript', TranscriptLog).refresh_renderable(reply)
            activity.update('')
            activity.display = False
            return
        activity.update(rows)
        activity.display = bool(pending) and self._approval_future is None

    def _clear_tool_activity(self) -> None:
        self._active_tools.clear()
        self._tool_started_at.clear()
        self._update_tool_activity()

    @on(events.Click,'#transcript')
    def show_tool_click(self,event):
        if self.screen.get_selected_text():
            return
        group_id = event.style.meta.get('nailong_group')
        if isinstance(group_id, int) and 1 <= group_id <= len(self._tool_groups):
            event.stop()
            group = self._tool_groups[group_id - 1]
            group.expanded = not group.expanded
            self.query_one('#transcript', TranscriptLog).refresh_renderable(group)
            return
        index=event.style.meta.get('nailong_tool')
        if isinstance(index,int):
            event.stop();self._show_tool_details(index)

    def action_toggle_tools(self):
        self._tools_expanded = not self._tools_expanded
        log = self.query_one('#transcript', TranscriptLog)
        for group in self._tool_groups:
            group.expanded = self._tools_expanded
            log.refresh_renderable(group)

    @work(group='tool-details',exclusive=True)
    async def _show_tool_details(self,index):
        if 1<=index<=len(self._tool_details):
            group = next((group for group in self._tool_groups
                          if any(entry_index == index for entry_index, _ in group.entries)), None)
            if group is None:
                group = ToolGroup(len(self._tool_groups) + 1, self._ui_theme, self.settings.api_key)
                group.add(index, self._tool_details[index - 1])
                self._tool_groups.append(group)
                self._write_activity(group)
            group.expanded = True
            if index in group.detail_indices:
                group.detail_indices.remove(index)
            else:
                group.detail_indices.add(index)
            self.query_one('#transcript', TranscriptLog).refresh_renderable(group)

    def _flush_tool_rows(self) -> None:
        """Close the current phase before an assistant reply or user message."""
        self._current_tool_group = None
        if not self._turn_tool_count:
            return
        count = self._turn_tool_count
        total_ms = self._pending_tool_ms
        self._turn_tool_count = 0
        self._pending_tool_ms = 0
        log = self.query_one("#transcript", RichLog)
        if count > 1 and self.actions.preferences.effective()['output_style'] == 'detailed':
            self._write_activity(indent(render_tool_group_header(count, total_ms, theme=self._ui_theme)))

    def _append_system(self, message: str) -> None:
        self._flush_tool_rows()
        self.query_one("#transcript", RichLog).write(
            Text(self._redact(message), style="dim")
        )

    def _clear_transcript(self):
        self.query_one('#transcript', RichLog).clear()
        self._tool_groups.clear()
        self._tool_details.clear()
        self._current_tool_group = None
        self._turn_tool_count = 0
        self._pending_tool_ms = 0
        self._turns_written = 0
        self._live_response = None

    def _append_user(self, message: str, *, count_turn: bool = True) -> None:
        log = self.query_one("#transcript", RichLog)
        self._flush_tool_rows()
        if count_turn and self._welcome_visible:
            self._clear_transcript()
            self._welcome_visible = False
        if count_turn and not self._conversation_started:
            self._conversation_started = True
            self._apply_layout(self.size.width, self.size.height)
        if count_turn and self._turns_written:
            log.write(Text(""))
        if count_turn:
            self._turns_written += 1
        user_line = render_user_message(message.replace("\n", "\n  "), theme=self._ui_theme, api_key=self.settings.api_key)
        log.write(user_line)
        if count_turn:
            self._session_metrics.turns += 1
            self._session_metrics.begin_context_turn()
        self._update_composer_metrics()

    def _append_assistant(self, message: str) -> None:
        log = self.query_one("#transcript", RichLog)
        self._flush_tool_rows()
        if self._live_response is not None:
            reply = self._live_response
            reply.text = self._redact(message)
            reply.progress = ''
            reply.pending_activity = None
            reply.finished = True
            log.refresh_renderable(reply)
            self._live_response = None
            return
        log.write(Text(""))
        log.register_reply(self._redact(message))
        log.write(render_role_header("assistant", theme=self._ui_theme))
        log.write(indent(Markdown(self._redact(message),code_theme=self._ui_theme.code_theme,style="none" if self._ui_theme.no_color else self._ui_theme.foreground)))

    def _clear_live_response(self) -> None:
        if self._live_response is not None:
            reply = self._live_response
            reply.finished = True
            reply.progress = '本轮已停止。' if not reply.text else ''
            reply.pending_activity = None
            self.query_one('#transcript', TranscriptLog).refresh_renderable(reply)
            self._live_response = None

    def _ensure_live_response(self):
        if self._live_response is None:
            self._live_response = AssistantTurn(self._ui_theme)
            log = self.query_one('#transcript', TranscriptLog)
            log.register_reply(self._live_response)
            log.write(Text(''))
            log.write(self._live_response)
        return self._live_response

    def _write_activity(self, content):
        log = self.query_one('#transcript', TranscriptLog)
        if self._live_response is not None:
            self._live_response.activities.append(content)
            log.refresh_renderable(self._live_response)
        else:
            log.write(content)

    def _show_tool_progress(self):
        reply = self._ensure_live_response()
        reply.text = ''
        reply.progress = '正在使用工具…'
        self.query_one('#transcript', TranscriptLog).refresh_renderable(reply)

    def _update_live_response(self, text: str) -> None:
        reply = self._ensure_live_response()
        reply.text = self._redact(text)
        reply.progress = ''
        self.query_one('#transcript', TranscriptLog).refresh_renderable(reply)

    def _stop_status_timer(self) -> None:
        if self._status_timer is not None:
            self._status_timer.stop()
            self._status_timer = None

    def _set_status(self, status: str) -> None:
        if status == 'thinking' and self._active_tools:
            status = 'running'
        if status != self._status or self._status_started_at is None:
            self._status_started_at = monotonic() if status in {'thinking', 'running'} else None
        self._status = status
        status_widget = self._status_widget
        if status_widget is None or not status_widget.is_attached:
            self._stop_status_timer()
            return
        if status in {'thinking', 'running'}:
            if self._status_timer is None:
                self._status_timer = self.set_interval(1.0 if self._reduced_motion else 0.12, self._animate_status)
            self._animate_status()
            return
        self._stop_status_timer()
        ascii_only = self._ui_theme.glyph_running == ">"
        no_color = self._ui_theme.no_color
        glyph, label, color = {
            "ready": ("+" if ascii_only else "●", "就绪", self._ui_theme.success),
            "waiting_approval": ("!" if ascii_only else "◆", "等待审批", self._ui_theme.role_user or self._ui_theme.accent),
            "error": ("!" if ascii_only else "●", "出错", self._ui_theme.danger),
            "running": (">" if ascii_only else "●", "执行中", self._ui_theme.accent),
            "paused": ("!" if ascii_only else "◆", "已暂停", self._ui_theme.role_user or self._ui_theme.accent),
        }.get(status, ("+" if ascii_only else "●", "就绪", self._ui_theme.success))
        indicator = Text()
        indicator.append(glyph, style=None if no_color else f"bold {color}")
        indicator.append(f" {label}")
        status_widget.update(indicator)

    def _spinner_frame(self):
        if self._reduced_motion:
            return '>' if self._ui_theme.glyph_running == '>' else '·'
        frames = ('|', '/', '-', '\\') if self._ui_theme.glyph_running == '>' else (
            '⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏')
        return frames[self._status_frame % len(frames)]

    def _animate_status(self) -> None:
        if self._status not in {'thinking', 'running'}:
            return
        status_widget = self._status_widget
        if status_widget is None or not status_widget.is_attached:
            self._stop_status_timer()
            return
        frame = self._spinner_frame()
        now = monotonic()
        elapsed = max(0, now - (self._status_started_at if self._status_started_at is not None else now))
        spinner = Text()
        spinner.append(
            frame,
            style=None if self._ui_theme.no_color else f"bold {self._ui_theme.accent}",
        )
        spinner.append((' 思考中…' if self._status == 'thinking' else ' 执行中…') + f' {elapsed:.1f}s')
        status_widget.update(spinner)
        reply = self._live_response
        if reply is not None and not reply.finished:
            reply.spinner = frame
            reply.elapsed_seconds = elapsed
            if not reply.text and not self._active_tools:
                self.query_one('#transcript', TranscriptLog).refresh_renderable(reply)
        if self._active_tools:
            self._update_tool_activity(frame=frame, now=now)
        self._status_frame += 1

    @on(TextArea.Changed, "#composer")
    def on_composer_changed(self, event: TextArea.Changed) -> None:
        self.query_one("#composer-placeholder", Static).display = not bool(event.text_area.text)
        self._update_suggestions(event.text_area.text)

    def _update_suggestions(self, value: str) -> None:
        menu = self.query_one("#command-menu", Static)
        composer = self.query_one("#composer", ChatInput)
        is_command_prefix = (
            value.startswith("/") and " " not in value.strip() and "\n" not in value
        )
        if composer.suggestions_dismissed:
            if is_command_prefix:
                self._filtered_commands = []
                composer.suggestions_active = False
                self._show_menu(False)
                return
            composer.suggestions_dismissed = False
        if value == self._selected_command_text:
            self._filtered_commands = []
            composer.suggestions_active = False
            self._show_menu(False)
            return
        self._selected_command_text = None
        if (
            not value.startswith(("/","$"))
            or " " in value.strip()
            or "\n" in value
        ):
            self._filtered_commands = []
            composer.suggestions_active = False
            self._show_menu(False)
            return

        query = value.lower()
        self._filtered_commands = [
            command for command in self.controller.specs() if command.name.startswith(query)
        ]
        if not self._filtered_commands:
            composer.suggestions_active = False
            self._show_menu(False)
            return

        self._selected_command = min(
            self._selected_command,
            len(self._filtered_commands) - 1,
        )
        composer.suggestions_active = True
        self._show_menu(True)
        rendered = Text()
        visible_rows = 3 if self._compact else 5 if self.size.height < 28 else 8
        first = max(0, self._selected_command - visible_rows + 1)
        for index in range(first, min(first + visible_rows, len(self._filtered_commands))):
            command = self._filtered_commands[index]
            selected = index == self._selected_command
            selected_style = None if self._ui_theme.no_color else f"bold {self._ui_theme.accent}"
            normal_style = None if self._ui_theme.no_color else "#d9e0e3"
            rendered.append("› " if selected else "  ", style=selected_style if selected else "dim")
            if self._compact:
                rendered.append(f"{command.name}\n", style=selected_style if selected else normal_style)
            else:
                rendered.append(command.name.ljust(16), style=selected_style if selected else normal_style)
                rendered.append(f"{command.description}\n", style=None if self._ui_theme.no_color else "#8b9ca3")
        hint = (
            f"↑↓ 选择  Enter 确认  Esc 关闭  {self._selected_command + 1}/{len(self._filtered_commands)}"
            if self._compact else
            f"↑↓ 选择   Tab/Enter 选中   Esc 关闭   {self._selected_command + 1}/{len(self._filtered_commands)}"
        )
        rendered.append(hint, style=None if self._ui_theme.no_color else "#8b9ca3")
        menu.update(rendered)

    @on(ChatInput.MoveSuggestion)
    def on_move_suggestion(self, event: ChatInput.MoveSuggestion) -> None:
        if not self._filtered_commands:
            return
        self._selected_command = (
            self._selected_command + event.delta
        ) % len(self._filtered_commands)
        self._update_suggestions(self.query_one("#composer", ChatInput).text)

    @on(ChatInput.ChooseSuggestion)
    def on_choose_suggestion(self) -> None:
        if not self._filtered_commands:
            return
        command = self._filtered_commands[self._selected_command]
        value = command.name + (" " if command.needs_argument or command.name == "/review" else "")
        composer = self.query_one("#composer", ChatInput)
        composer.text = value
        composer.cursor_location = (0, len(value))
        self._selected_command_text = value
        composer.suggestions_dismissed = False
        composer.suggestions_active = False
        self._show_menu(False)

    @on(ChatInput.DismissSuggestions)
    def on_dismiss_suggestions(self) -> None:
        composer = self.query_one("#composer", ChatInput)
        self._filtered_commands = []
        composer.suggestions_active = False
        self._show_menu(False)

    @on(ChatInput.Submitted)
    def on_input_submitted(self, event: ChatInput.Submitted) -> None:
        message = event.text.strip()
        if not message:
            return
        composer = self.query_one("#composer", ChatInput)
        if self._dispatch(message) is False: return
        composer.text = ""
        composer.cursor_location = (0, 0)
        self._selected_command_text = None
        composer.suggestions_dismissed = False
        self._filtered_commands = []
        composer.suggestions_active = False
        self._show_menu(False)

    def _dispatch(self, message: str) -> None:
        if getattr(self, '_project_switching', False):
            self._append_system('正在切换项目，请稍后输入。')
            return False
        try:
            request = self.controller.resolve(message)
            if request.command == '/help':
                section = request.argument or 'general'
                if section not in {'general', 'commands', 'custom', 'skills'}:
                    self._append_system('用法：/help [general|commands|custom|skills]')
                    return
                self._show_menu(False)
                self._show_help(section)
                return
            information = None if self.actions.handles(request) else self.controller.information(
                request, self.service, self.thread_id, completed_turns=self.completed_turns,
            )
            if information is not None:
                self._append_system(information)
                return
        except Exception as error:
            self._append_system(self._friendly_error(error))
            return
        if request.command in IMMEDIATE_COMMANDS and self.actions.handles(request):
            self._immediate_request(request)
            return
        if request.kind=='prompt' or self.actions.handles(request) or request.command in {'/goal','/compact','/rewind'}:
            return self._enqueue_request(request)
        if request.command in IDENTITY_COMMANDS and self.session_runner.busy:
            self._append_system('请先 /stop 并 /queue clear，再切换项目、会话或退出。')
            return
        command, argument = request.command, request.argument
        if command == "/history":
            self._show_history(replace=True)
        elif command == "/sessions":
            store = getattr(self.service, "session_store", None)
            sessions = store.list_sessions() if store is not None else []
            if not sessions:
                self._append_system("当前项目还没有已保存的会话。")
                return
            for index, session in enumerate(sessions, start=1):
                self._append_system(
                    f"{index}. {session['thread_id']}  {session['updated_at']}  {session['summary']}"
                )
        elif command == "/resume":
            store = getattr(self.service, "session_store", None)
            if store is None or not argument:
                self._append_system("用法：/resume <会话 ID 或 /sessions 中的序号>")
                return
            try:
                old_thread_id = self.thread_id
                self.thread_id = store.resolve_session(argument,**({"query":self.actions.session_query} if self.actions.session_query else {}))
            except ValueError as error:
                self._append_system(str(error))
                return
            self.session_runner.rebind(self.settings.project_root,self.thread_id)
            self._update_session_info()
            self._reload_session_metrics()
            if self._show_history(replace=True):
                self._append_system(f"已恢复会话 {self.thread_id}。")
            else:
                self.thread_id = old_thread_id
                self._update_session_info()
                self._reload_session_metrics()
        elif command == "/rewind":
            if argument:
                self._append_system("用法：/rewind")
                return
            self._rewind_session(self.thread_id)
        elif command == "/compact":
            self._compact_session(self.thread_id)
        elif command == "/goal":
            self._dispatch_goal(argument)
        elif command == "/clear":
            self.thread_id = uuid.uuid4().hex
            self.session_runner.rebind(self.settings.project_root,self.thread_id)
            self._conversation_started = False
            self._update_session_info()
            self._reload_session_metrics()
            self._clear_transcript()
            self._append_system("已开始一个新的会话。")
            self._show_welcome()
        elif command == "/project":
            if not argument:
                self._append_system("用法：/project <项目目录>")
                return
            self._project_switching = True
            self._switch_project(argument)
        elif command == "/exit":
            self.exit()

    @work(group='project-switch', exclusive=True)
    async def _switch_project(self, argument):
        new_factory = None
        adopted = False
        try:
            new_root = select_project_root(argument)
            new_settings = replace(self.settings, project_root=new_root)
            new_factory = AgentRuntimeFactory(new_settings)
            new_service = AgentService(new_factory, api_key=new_settings.api_key,
                permission_engine=PermissionEngine(new_root), permission_mode=self.permission_mode)
            old_factory = getattr(self.service, 'runtime_factory', None)
            if old_factory is not None:
                aclose = getattr(old_factory, 'aclose', None)
                if callable(aclose):
                    value = aclose()
                    if inspect.isawaitable(value):
                        await value
                close = getattr(old_factory, 'close', None)
                if callable(close):
                    close()
                if old_factory in self.runtime_factories:
                    self.runtime_factories.remove(old_factory)
            if inspect.iscoroutinefunction(getattr(new_factory, "aclose", None)) and new_factory not in self.runtime_factories:
                self.runtime_factories.append(new_factory)
            self.settings = getattr(new_factory,'settings',None) or new_settings
            self.controller = CommandController(self.settings)
            self.controller.skill_registry=getattr(new_factory,'skill_registry',None)
            self.actions = CommandActions(self.controller)
            if getattr(new_factory,'preferences',None) is not None: self.actions.preferences=new_factory.preferences
            self.service = new_service
            adopted = True
            self.service.ui_name='textual'
            self.thread_id = uuid.uuid4().hex
            self.session_runner.rebind(self.settings.project_root,self.thread_id)
            self._conversation_started = False
            self._update_session_info()
            self._reload_session_metrics()
            self._clear_transcript()
            self._append_system(f"已切换到项目：{new_root}。已开始新的会话。")
            self._show_welcome()
        except Exception as error:
            self._append_system(f"切换项目失败：{self._friendly_error(error)}")
        finally:
            self._project_switching = False
            if new_factory is not None and not adopted:
                await new_factory.aclose()
                new_factory.close()

    @work(group='help', exclusive=True)
    async def _show_help(self, section='general'):
        chosen = await self._wait_panel(HelpScreen(self.controller.specs(), self._ui_theme,
            api_key=self.settings.api_key, initial_tab=section))
        composer = self.query_one('#composer', ChatInput)
        if chosen:
            spec = next((spec for spec in self.controller.specs() if spec.name == chosen), None)
            composer.text = chosen + (' ' if spec and spec.needs_argument else '')
            composer.cursor_location = (0, len(composer.text))
            self._selected_command_text = composer.text
            composer.suggestions_dismissed = True
            self._show_menu(False)
        if self._interaction_panel is None and self.screen is composer.screen and not composer.disabled:
            composer.focus()

    def _show_history(self, *, replace: bool = False) -> bool:
        try:
            messages = self.service.get_history(self.thread_id)
        except Exception as error:
            self._append_system(f"读取历史失败：{self._friendly_error(error)}")
            return False
        if replace:
            self._clear_transcript()
            self._welcome_visible = False
            self._conversation_started = True
            self._apply_layout(self.size.width, self.size.height)
        if not messages:
            self._append_system("当前会话暂无历史信息。")
            return True
        # Older event logs cannot distinguish one visible /goal or /plan input
        # from the internal model rounds that followed it.
        self._session_metrics.turns = sum(
            role == "user" and bool(str(content).strip()) for role, content in messages
        )
        self._update_composer_metrics()
        for role, content in messages:
            if not str(content).strip() or role == "tool" or role.startswith("tool("):
                continue
            if role == "assistant" and _is_tool_call_payload(content):
                continue
            if role == "user":
                self._append_user(content, count_turn=False)
            elif role == "assistant":
                self._append_assistant(content)
            else:
                self._append_system(f"{role}: {content}")
        return True

    def _dispatch_goal(self, argument: str) -> None:
        factory = getattr(self.service, "runtime_factory", None)
        try:
            goal, messages = self.controller.goal(argument, factory, self.thread_id)
            for message in messages:
                self._append_system(message)
            if goal is None:
                return
            display_message = "/goal " + (argument or goal.objective)
            self._append_user(display_message)
            self.query_one("#composer", ChatInput).disabled = False
            self._run_goal(goal, self.controller.cost_estimator, display_message)
        except Exception as error:
            self._append_system(f"目标运行失败：{self._friendly_error(error)}")

    def _render_flow_event(self, event, tracker: StepTracker, streamed: list[str]) -> None:
        kind = event.kind
        data = event.data or {}
        self._observe_usage(event)
        if kind == "plan_draft":
            self._append_system("待审批执行计划：")
            self.query_one("#transcript", RichLog).write(Markdown(self._redact(str(data.get("text", "")))))
            return
        if kind in {"notice", "goal_status"}:
            if kind == "goal_status":
                self.completed_turns += 1
            self._append_system(str(data.get("text", "")))
            return
        if kind == 'delivery':
            # The same report is carried by final; render after the answer.
            return
        if kind == 'status' and data.get('status') == 'thinking':
            self._ensure_live_response()
        if kind == "token":
            self._flush_tool_rows()
            streamed.append(str(data.get("text", "")))
            self._update_live_response("".join(streamed))
        elif kind == 'tool_start':
            streamed.clear()
            self._show_tool_progress()
        elif kind == "final":
            answer = str(data.get("text", "")) or "".join(streamed)
            if answer:
                self._append_assistant(answer)
            else:
                self._clear_live_response()
            if data.get('delivery'):
                from nailong.core.delivery import render_delivery_report
                self._append_system(render_delivery_report(data['delivery']))
            streamed.clear()
        elif kind in {"error", "hook_blocked", "hook_feedback"}:
            self._append_system(str(data.get("message") or data.get("reason") or data.get("text") or ""))
        elif kind == 'task_paused':
            self._handle_task_paused(data)
        rows = tracker.observe(event)
        self._append_activity(event, rows)

    async def _compact_session(self, thread_id: str) -> None:
        try:
            result = await self.service.compact_context(thread_id)
            if result.get("compacted"):
                self._append_system(
                    f"已压缩较早工具结果：约 {result['before_tokens']} → {result['after_tokens']} tokens。"
                )
            else:
                self._append_system(f"未压缩上下文：{result.get('reason', '无可压缩内容')}。")
        except Exception as error:
            self._append_system(f"压缩失败：{self._friendly_error(error)}")

    async def _run_plan(self, prompt_message: str, allowed_tools=None, history_display=None) -> None:
        factory = getattr(self.service, "runtime_factory", None)
        tracker = StepTracker(
            context_window=configured_context_window(self.settings.model, self.settings.project_root),
            api_key=self.settings.api_key, theme=self._ui_theme, width=max(20, self.size.width - 4),
        )
        streamed: list[str] = []

        async def confirm(draft: str) -> str:
            return await self._wait_panel(PlanReviewScreen(self._redact(draft)))

        async def edit(draft: str) -> str:
            revised = await self._wait_panel(PlanEditScreen(self._redact(draft)))
            return revised if revised is not None else draft

        try:
            await run_plan_flow(
                self.service, factory, thread_id=self.thread_id,
                config={"configurable": {"thread_id": self.thread_id}, "recursion_limit": 40},
                prompt_message=prompt_message,
                allowed_tools=allowed_tools, history_display=history_display,
                emit=lambda event: self._render_flow_event(event, tracker, streamed),
                confirm=confirm, edit=edit,
                approval=self._request_approval, status=self._set_status,
            )
            self.completed_turns += 1
        except asyncio.CancelledError:
            from ui.actions import pause_session_goal
            pause_session_goal(self.service,self.thread_id)
            raise
        except Exception as error:
            self.query_one("#transcript", RichLog).write(render_completion(tracker.steps, ok=False, theme=self._ui_theme))
            self._append_system(f"计划运行失败：{self._friendly_error(error)}")
        finally:
            self._clear_live_response()
            self._clear_tool_activity()
            store=getattr(self.service,'session_store',None)
            if store and any(row.get('kind')=='turn_start' for row in store.read_events(self.thread_id)):
                self._reload_session_metrics()
            composer = self.query_one("#composer", ChatInput)
            composer.disabled = False
            if self._interaction_panel is None and self.screen is composer.screen:
                self.set_focus(composer)
            self._set_status("paused" if self.session_runner.state == 'paused' else "ready")

    async def _run_goal(self, goal, estimator: CostEstimator, display_message: str | None = None) -> None:
        factory = getattr(self.service, "runtime_factory", None)
        tracker = StepTracker(
            context_window=configured_context_window(self.settings.model, self.settings.project_root),
            api_key=self.settings.api_key, theme=self._ui_theme, width=max(20, self.size.width - 4),
        )
        streamed: list[str] = []
        try:
            await drive_goal(
                self.service, factory, factory.goal_store, estimator, goal,
                thread_id=self.thread_id,
                emit=lambda event: self._render_flow_event(event, tracker, streamed),
                status=self._set_status, approval=self._request_approval,
                history_display=display_message,
            )
        except Exception as error:
            self.query_one("#transcript", RichLog).write(render_completion(tracker.steps, ok=False, theme=self._ui_theme))
            self._append_system(f"目标运行失败：{self._friendly_error(error)}")
        finally:
            self._clear_live_response()
            self._clear_tool_activity()
            composer = self.query_one("#composer", ChatInput)
            composer.disabled = False
            if self._interaction_panel is None and self.screen is composer.screen:
                self.set_focus(composer)
            self._set_status("paused" if self.session_runner.state == 'paused' else "ready")

    def _start_turn(
        self,
        prompt: str,
        *,
        profile: str,
        target_path: str | None = None,
        display_message: str | None = None,
        allowed_tools=None,
    ) -> None:
        self._append_user(display_message or prompt)
        self.query_one("#composer", ChatInput).disabled = False
        config = {
            "configurable": {"thread_id": self.thread_id},
            "recursion_limit": 40,
        }
        self._run_turn(prompt, config, profile, target_path, allowed_tools, display_message)

    async def _run_turn(
        self,
        prompt: str,
        config: dict,
        profile: str,
        target_path: str | None,
        allowed_tools=None,
        history_display=None,
        review_paths=None,
    ) -> None:
        tracker = StepTracker(
            context_window=configured_context_window(self.settings.model, self.settings.project_root),
            api_key=self.settings.api_key,
            theme=self._ui_theme,
            width=max(20, self.size.width - 4),
        )
        completion_rows = []
        failed = False
        try:
            if callable(getattr(self.service, "stream_turn", None)):
                answer = ""
                delivery = None
                streamed = ""
                self._ensure_live_response()
                async for event in self.service.stream_turn(
                    prompt,
                    config,
                    profile=profile,
                    target_path=target_path,
                    allowed_tools=allowed_tools, history_display=history_display,
                    **({"review_paths":review_paths} if review_paths is not None else {}),
                    approval_handler=self._request_approval,
                    status_handler=self._set_status,
                ):
                    self._observe_usage(event)
                    if event.kind == "token":
                        self._flush_tool_rows()
                        streamed += event.data.get("text", "")
                        self._update_live_response(streamed)
                    elif event.kind == 'tool_start':
                        streamed = ''
                        self._show_tool_progress()
                    elif event.kind == "final":
                        answer = event.data.get("text", "")
                        delivery = event.data.get('delivery') or delivery
                    elif event.kind == 'delivery':
                        delivery = event.data
                    elif event.kind == "error":
                        failed = True
                        self._append_system(str(event.data.get("message", "请求失败")))
                    elif event.kind == 'task_paused':
                        self._handle_task_paused(event.data)
                    rows = tracker.observe(event)
                    if event.kind == "final":
                        completion_rows = rows
                    else:
                        self._append_activity(event, rows)
                answer = answer or ("" if failed else streamed)
                if failed:
                    completion_rows = [render_completion(tracker.steps, ok=False, theme=self._ui_theme)]
            else:
                answer = await self.service.run_turn(
                    prompt,
                    config,
                    profile=profile,
                    target_path=target_path,
                    allowed_tools=allowed_tools, history_display=history_display,
                    **({"review_paths":review_paths} if review_paths is not None else {}),
                    approval_handler=self._request_approval,
                    status_handler=self._set_status,
                )
                completion_rows = [render_completion(tracker.steps, theme=self._ui_theme)]
            if answer:
                self._append_assistant(answer)
            if callable(getattr(self.service, 'stream_turn', None)) and delivery:
                from nailong.core.delivery import render_delivery_report
                self._append_system(render_delivery_report(delivery))
            if failed or self.actions.preferences.effective()['output_style'] == 'detailed':
                for row in completion_rows:
                    self.query_one("#transcript", RichLog).write(row)
            self.completed_turns += 1
        except (TurnRecursionLimitError, GraphRecursionError) as error:
            stats = getattr(error, 'stats', {})
            self.query_one('#transcript',RichLog).write(render_completion(
                stats.get('model_calls') or tracker.steps, ok=False, theme=self._ui_theme,
                tool_calls=stats.get('tool_calls', tracker.tool_calls)))
            self._append_system(self._friendly_error(error))
            self.session_runner.state='paused'
            self._append_system('本轮已停止，会话保留；待执行输入已暂停。')
        except asyncio.CancelledError:
            from ui.actions import pause_session_goal
            pause_session_goal(self.service,self.thread_id)
            raise
        except Exception as error:
            self.query_one("#transcript", RichLog).write(render_completion(tracker.steps, ok=False, theme=self._ui_theme))
            self._append_system(self._friendly_error(error))
        finally:
            self._clear_live_response()
            self._clear_tool_activity()
            store=getattr(self.service,'session_store',None)
            if store and any(row.get('kind')=='turn_start' for row in store.read_events(self.thread_id)):
                self._reload_session_metrics()
            composer = self.query_one("#composer", ChatInput)
            composer.disabled = False
            if self._interaction_panel is None and self.screen is composer.screen:
                self.set_focus(composer)
            self._set_status("paused" if self.session_runner.state == 'paused' else "ready")

    def _handle_task_paused(self, data):
        self.session_runner.state = 'paused'
        reason = str((data or {}).get('reason') or (data or {}).get('message') or '需要用户处理。')
        self._append_system(reason + ' 待执行输入已暂停；用 /queue resume 恢复。')
        self._set_status('paused')

    async def _rewind_session(self, thread_id: str) -> None:
        self._append_system(
            "回退会舍弃当前用户轮次及之后的对话状态；"
            "已经执行的文件修改和命令不会撤销。"
        )
        confirmed = await self._wait_panel(RewindConfirmationScreen())
        if not confirmed:
            self._append_system("已取消回退。")
            return
        try:
            removed = await self.service.rewind(thread_id)
            if removed is None:
                self._append_system("当前会话没有可回退的用户轮次。")
                return
            self._reload_session_metrics()
            if self._show_history(replace=True):
                self._append_system(
                    f"对话状态已回退到上一轮开始前，移除了 {removed} 条消息。"
                    "已执行的文件和命令操作不会撤销。"
                )
        except Exception as error:
            self._append_system(f"回退失败：{self._friendly_error(error)}")

    async def _request_approval(self, action: dict, index: int, total: int) -> str:
        main_screen = self.screen
        panel = self.query_one('#approval-panel', ApprovalPrompt)
        composer = self.query_one('#composer', ChatInput)
        future = asyncio.get_running_loop().create_future()
        self._approval_future = future
        self._approval_closed.clear()
        was_disabled = None
        self._set_status('waiting_approval')
        try:
            # A reader/editor can finish locally; required approval then owns the UI.
            while self._interaction_panel is not None:
                await self._interaction_closed.wait()
            was_disabled = composer.disabled
            composer.disabled = True
            self.screen.add_class('panel-open')
            self._show_menu(False)
            panel.configure(action, index, total, self.settings.project_root, self._ui_theme, self.settings.api_key)
            panel.set_class(self._compact, 'compact')
            panel.display = True
            self.query_one('#tool-activity', Static).display = False
            panel.focus_choices()
            return await future
        finally:
            self._approval_future = None
            panel.display = False
            main_screen.remove_class('panel-open')
            if was_disabled is not None:
                composer.disabled = was_disabled
            self._approval_closed.set()
            if self.is_running:
                self._update_tool_activity()
            if self.is_running and self._interaction_panel is None and not composer.disabled:
                composer.focus()

    @on(ApprovalPrompt.Decided)
    def approval_decided(self, event):
        event.stop()
        if self._approval_future is not None and not self._approval_future.done():
            self._approval_future.set_result(event.decision)

    def _size_interaction(self):
        if self._interaction_panel is not None:
            composer_height = 3
            self.query_one('#interaction-host').styles.height = min(
                self._interaction_panel.preferred_height, max(5, self.size.height - composer_height - 5))

    async def _wait_panel(self, panel, *, apply=None):
        while self._interaction_panel is not None or self._approval_future is not None:
            await (self._interaction_closed if self._interaction_panel is not None else self._approval_closed).wait()
        main_screen = self.screen
        host = self.query_one('#interaction-host', Vertical)
        composer = self.query_one('#composer', ChatInput)
        previous_focus = self.focused
        transcript = self.query_one('#transcript', TranscriptLog)
        follow, position = transcript.is_vertical_scroll_end, transcript.scroll_y
        was_disabled = composer.disabled
        self._interaction_closed.clear()
        self.screen.add_class('panel-open')
        self._interaction_panel = panel
        self._interaction_future = asyncio.get_running_loop().create_future()
        host.display = True
        self._size_interaction()
        composer.disabled = True
        try:
            await host.mount(panel)
            panel.apply_theme(self._ui_theme)
            while True:
                value = await self._interaction_future
                if value is None or apply is None:
                    return value
                try:
                    result = apply(value)
                    if inspect.isawaitable(result):
                        await result
                    return value
                except (ValueError, OSError) as error:
                    panel.show_error(self._redact(str(error)))
                    self._interaction_future = asyncio.get_running_loop().create_future()
        finally:
            await panel.remove()
            host.display = False
            composer.disabled = was_disabled
            main_screen.remove_class('panel-open')
            self._interaction_panel = None
            self._interaction_future = None
            self._interaction_closed.set()
            if follow:
                transcript.scroll_end(animate=False)
            else:
                transcript.scroll_to(y=position, animate=False)
            if self.is_running and previous_focus is not None and previous_focus.is_attached:
                previous_focus.focus()
            elif self.is_running:
                composer.focus()

    @on(InteractionPanel.Resolved)
    def interaction_resolved(self, event):
        event.stop()
        if event.panel is self._interaction_panel and self._interaction_future is not None and not self._interaction_future.done():
            self._interaction_future.set_result(event.value)

    def _update_session_info(self) -> None:
        self._apply_layout(self.size.width, self.size.height)

    def _friendly_error(self, error: Exception) -> str:
        if isinstance(error, (TurnRecursionLimitError, GraphRecursionError)):
            return self._redact(str(error)) if isinstance(error, TurnRecursionLimitError) else "本轮内部执行达到上限，已停止并保留会话。"
        if isinstance(error, ConfigurationError):
            return str(error)
        detail = self._redact(str(error)).replace("\n", " ").strip()
        if len(detail) > 500:
            detail = detail[:500] + "…"
        return f"本轮请求失败（{type(error).__name__}）：{detail or '请检查网络和模型配置。'}"


def run_tui(
    settings: Settings,
    permission_mode: str = "default",
    *,
    continue_session: bool = False,
    resume_session: str | None = None,
) -> None:
    """Construct the scoped Agent service and launch the full-screen interface."""
    with local_tools.use_project_root(settings.project_root):
        factory = AgentRuntimeFactory(settings)
        service = AgentService(
            factory,
            api_key=settings.api_key,
            permission_engine=PermissionEngine(settings.project_root),
            permission_mode=permission_mode,
        )
        store = factory.session_store
        thread_id = uuid.uuid4().hex
        sessions = store.list_sessions()
        try:
            if resume_session is not None:
                thread_id = store.resolve_session(resume_session)
            elif continue_session and sessions:
                thread_id = sessions[0]["thread_id"]
        except ValueError:
            factory.close()
            raise
        TerminalAgentApp(
            service,
            settings,
            permission_mode=permission_mode,
            thread_id=thread_id,
        ).run()
