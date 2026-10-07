"""Compact run-setting selector, returning a value without mutating state."""
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from ui.interaction import InteractionPanel, ChoiceList
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


class SettingScreen(InteractionPanel):
    conversation_panel = True
    BINDINGS = [Binding('escape', 'cancel', priority=True, show=False)]
    DEFAULT_CSS = """
    #setting-dialog { height: 100%; width: 100%; padding: 0; }
    #setting-title { height: 1; color: $accent; text-style: bold; }
    #setting-options { height: 1fr; border: none; padding: 0; background: transparent; }
    #setting-options:focus { border: none; }
    #setting-keys { height: 1; color: $text-muted; }
    """

    def __init__(self, title, current, options):
        super().__init__()
        self.title_text, self.current, self.options = title, current, options

    def compose(self) -> ComposeResult:
        with Vertical(id='setting-dialog'):
            yield Static(self.title_text, id='setting-title', markup=False)
            rows = [Option(Text(label+(' · 当前' if key == self.current else '')+'\n  '+description), id=key)
                    for key,label,description in self.options]
            yield ChoiceList(*rows, id='setting-options')
            yield Static('↑↓ 选择 · Enter 确认 · Esc 取消', id='setting-keys')

    def on_mount(self):
        theme = getattr(self.app, '_ui_theme', None)
        if theme is not None:
            self.apply_theme(theme)
        menu = self.query_one('#setting-options', OptionList)
        keys = [row[0] for row in self.options]
        menu.highlighted = keys.index(self.current) if self.current in keys else 0
        menu.focus()

    @on(OptionList.OptionSelected)
    def option_selected(self, event):
        event.stop()
        self.dismiss(event.option.id)

    def action_cancel(self):
        self.dismiss(None)
