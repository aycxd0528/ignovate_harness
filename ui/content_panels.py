"""Inline reading, editing and explicit confirmation views."""
from rich.markdown import Markdown
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.widgets import Static, TextArea, Button
from ui.interaction import InteractionPanel

class CopyReplyScreen(InteractionPanel):
    """A stable, selectable raw-text snapshot while the model keeps streaming."""

    preferred_height = 18
    BINDINGS = [
        Binding('escape', 'close', show=False, priority=True),
        Binding('ctrl+c,super+c', 'copy_selected', show=False, priority=True),
        Binding('ctrl+a,super+a', 'select_all', show=False, priority=True),
        Binding('f5', 'refresh_snapshot', show=False, priority=True),
        Binding('alt+left', 'previous', show=False, priority=True),
        Binding('alt+right', 'next', show=False, priority=True),
    ]
    DEFAULT_CSS = """

    #copy-dialog { width: 100%; height: 100%; padding: 0; }
    #copy-title { height: 1; text-style: bold; }
    #copy-help { height: 1; margin-bottom: 0; }
    #copy-body { height: 1fr; border: none; }
    #copy-status { height: 1; margin-top: 0; }
    #copy-controls { height: 1; margin-top: 0; }
    #copy-controls Button { width: auto; min-width: 0; height: 1; padding: 0 1; margin-right: 1; border: none; }
    """

    def __init__(self, source, theme):
        super().__init__()
        self._source = source
        self._replies = source()
        self._index = len(self._replies) - 1
        self._theme = theme

    def compose(self) -> ComposeResult:
        with Vertical(id='copy-dialog'):
            yield Static('', id='copy-title', markup=False)
            yield Static('正文快照 · F5 刷新 · Esc 返回', id='copy-help')
            yield TextArea(read_only=True, soft_wrap=True, show_line_numbers=False,
                           highlight_cursor_line=False, id='copy-body')
            yield Static('', id='copy-status', markup=False)
            with Horizontal(id='copy-controls'):
                for label, identifier in [('上一条', 'copy-previous'), ('下一条', 'copy-next'),
                        ('刷新', 'copy-refresh'), ('返回', 'copy-close')]:
                    yield Button(label, id=identifier, compact=True)

    def on_mount(self) -> None:
        theme = self._theme
        self.query_one('#copy-dialog').styles.background = theme.surface
        self.query_one('#copy-dialog').styles.border = ('none', theme.border)
        self.query_one('#copy-help').styles.color = theme.muted
        self.query_one('#copy-status').styles.color = theme.muted
        self.query_one('#copy-body').styles.background = theme.background
        self.query_one('#copy-body').styles.color = theme.foreground
        self._show_reply()
        self.query_one('#copy-body', TextArea).focus()

    def _show_reply(self) -> None:
        total = len(self._replies)
        text = self._replies[self._index] if total else ''
        self.query_one('#copy-title', Static).update(
            f'正文 · 第 {self._index + 1 if total else 0}/{total} 条 · Markdown 原文')
        self.query_one('#copy-body', TextArea).load_text(text)
        self.query_one('#copy-previous', Button).disabled = self._index <= 0
        self.query_one('#copy-next', Button).disabled = self._index >= total - 1
        self.query_one('#copy-status', Static).update('正文不包含角色标记和工具记录。')

    def action_previous(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._show_reply()

    def action_next(self) -> None:
        if self._index < len(self._replies) - 1:
            self._index += 1
            self._show_reply()

    def action_refresh_snapshot(self) -> None:
        self._replies = self._source()
        self._index = len(self._replies) - 1
        self._show_reply()
        self.query_one('#copy-body', TextArea).focus()

    def action_select_all(self) -> None:
        body = self.query_one('#copy-body', TextArea)
        body.select_all()
        body.focus()

    async def _copy(self, text: str) -> None:
        if not text:
            self.query_one('#copy-status', Static).update('请先选择正文，或按 Ctrl+A 全选。')
            self.query_one('#copy-body', TextArea).focus()
            return
        native = await self.app._copy_text(text)
        status = f'已复制 {len(text):,} 个字符。' if native else '已发送复制请求；系统剪贴板取决于终端支持。'
        if self.is_mounted and self.is_attached:
            self.query_one('#copy-status', Static).update(status)

    async def action_copy_selected(self) -> None:
        await self._copy(self.query_one('#copy-body', TextArea).selected_text)

    async def action_copy_all(self) -> None:
        await self._copy(self.query_one('#copy-body', TextArea).text)

    @on(Button.Pressed)
    async def copy_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        actions = {'copy-previous': self.action_previous, 'copy-next': self.action_next,
                   'copy-refresh': self.action_refresh_snapshot, 'copy-close': self.action_close}
        if event.button.id == 'copy-selected':
            await self.action_copy_selected()
        elif event.button.id == 'copy-all':
            await self.action_copy_all()
        elif event.button.id in actions:
            actions[event.button.id]()

    def action_close(self) -> None:
        self.dismiss(None)


class RewindConfirmationScreen(InteractionPanel):
    """Explain the dialog history that will be discarded before rewind."""

    BINDINGS = [Binding("escape", "cancel", show=False, priority=True)]
    DEFAULT_CSS = """

    #rewind-dialog { width: 100%; height: 100%; padding: 0; }
    #rewind-title { height: 1; color: #ffd08b; text-style: bold; }
    #rewind-details { height: 1fr; padding: 0; color: #d8e1e2; }
    #rewind-buttons { height: 1; align-horizontal: left; }
    #rewind-buttons Button { margin-left: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="rewind-dialog"):
            yield Static("确认回退对话状态？", id="rewind-title")
            yield Static(
                "这会舍弃当前用户轮次及之后的对话状态。"
                "已经执行的文件修改和命令不会撤销。",
                id="rewind-details",
                markup=False,
            )
            with Horizontal(id="rewind-buttons"):
                yield Button("取消", id="cancel")
                yield Button("回退", id="confirm", variant="warning")

    def on_mount(self) -> None:
        self.set_focus(self.query_one("#cancel", Button))

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class PlanReviewScreen(InteractionPanel):
    """Require an explicit decision before persisting or executing a draft."""

    BINDINGS = [Binding("escape", "reject", show=False, priority=True)]
    DEFAULT_CSS = """

    #plan-review-dialog { width: 100%; height: 100%; padding: 0; }
    #plan-review-title { height: 1; color: #ffd08b; text-style: bold; }
    #plan-review-content { height: 1fr; }
    #plan-review-buttons { height: 1; align-horizontal: left; }
    #plan-review-buttons Button { margin-left: 1; }
    """

    def __init__(self, draft: str) -> None:
        super().__init__()
        self.draft = draft

    def compose(self) -> ComposeResult:
        with Vertical(id="plan-review-dialog"):
            yield Static("待审批执行计划", id="plan-review-title")
            with VerticalScroll(id="plan-review-content"):
                yield Static(Markdown(self.draft))
            with Horizontal(id="plan-review-buttons"):
                yield Button("拒绝", id="plan-reject", variant="error")
                yield Button("编辑", id="plan-edit")
                yield Button("批准执行", id="plan-approve", variant="success")

    def on_mount(self) -> None:
        self.set_focus(self.query_one("#plan-reject", Button))

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss({"plan-approve": "approve", "plan-edit": "edit"}.get(event.button.id, "reject"))

    def action_reject(self) -> None:
        self.dismiss("reject")


class PlanEditScreen(InteractionPanel):
    """Edit a staged plan in memory before a second approval."""

    BINDINGS = [Binding("escape", "cancel", show=False, priority=True)]
    DEFAULT_CSS = """

    #plan-edit-dialog { width: 100%; height: 100%; padding: 0; }
    #plan-edit-title { height: 1; color: #ffd08b; text-style: bold; }
    #plan-edit-content { height: 1fr; border: round #285254; }
    #plan-edit-buttons { height: 1; align-horizontal: left; }
    #plan-edit-buttons Button { margin-left: 1; }
    """

    def __init__(self, draft: str, *, title: str = '编辑计划草稿；保存后仍需确认执行') -> None:
        super().__init__()
        self.draft = draft
        self.title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="plan-edit-dialog"):
            yield Static(self.title, id="plan-edit-title")
            yield TextArea(self.draft, id="plan-edit-content")
            with Horizontal(id="plan-edit-buttons"):
                yield Button("取消", id="plan-edit-cancel")
                yield Button("保存草稿", id="plan-edit-save", variant="primary")

    def on_mount(self) -> None:
        self.set_focus(self.query_one("#plan-edit-content", TextArea))

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "plan-edit-save":
            self.dismiss(self.query_one("#plan-edit-content", TextArea).text)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
