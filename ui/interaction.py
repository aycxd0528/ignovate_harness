"""Result-bearing interactions that participate in the conversation layout."""
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Button, Input, OptionList, TextArea
from rich.text import Text
from rich.style import Style


class ChoiceList(OptionList):
    """Semantic selection colors and a cursor independent of the current value."""
    semantic_theme = None

    def get_component_styles(self, *names):
        styles = super().get_component_styles(*names)
        theme = self.semantic_theme
        if theme is not None and any('highlight' in name or 'hover' in name for name in names):
            styles.color = theme.foreground
            styles.background = theme.surface
            styles.text_style = 'bold reverse' if theme.no_color else 'bold'
        return styles

    def get_component_rich_style(self, *names, partial=False, default=None):
        theme = self.semantic_theme
        if theme is not None and any('highlight' in name or 'hover' in name for name in names):
            return (Style(bold=True, reverse=True) if theme.no_color else
                    Style(color=theme.foreground, bgcolor=theme.surface, bold=True))
        return super().get_component_rich_style(*names, partial=partial, default=default)

    def set_semantic_theme(self, theme):
        self.semantic_theme = theme
        self._rich_style_cache.clear()
        self._paint_cursor()
        self.refresh()

    def _paint_cursor(self):
        theme = self.semantic_theme
        cursor = '> ' if theme is not None and theme.glyph_running == '>' else '› '
        for index in range(self.option_count):
            prompt = self.get_option_at_index(index).prompt
            if isinstance(prompt, Text):
                row = prompt.copy()
                if row.plain.startswith(('› ', '> ', '  ')):
                    row = row[2:]
                row = Text(cursor if index == self.highlighted else '  ') + row
                self.replace_option_prompt_at_index(index, row)

    def on_option_list_option_highlighted(self, event):
        if event.option_list is self:
            self._paint_cursor()


class FieldInput(Input):
    semantic_theme = None

    def get_component_rich_style(self, *names, partial=False, default=None):
        if self.semantic_theme is not None and 'input--placeholder' in names:
            return Style(color=None if self.semantic_theme.no_color else self.semantic_theme.muted)
        return super().get_component_rich_style(*names, partial=partial, default=default)


class InteractionPanel(Vertical):
    preferred_height = 14
    DEFAULT_CSS = """
    InteractionPanel { height: 100%; padding: 0 1; border-top: solid $accent; }
    InteractionPanel Button { height: 1; min-width: 0; width: auto;
        border: none; padding: 0 1; margin-right: 1; }
    InteractionPanel Button:focus { text-style: bold reverse; }
    InteractionPanel Input:focus { text-style: underline; }
    InteractionPanel Input { height: 3; margin: 0; }
    """

    class Resolved(Message):
        def __init__(self, panel, value):
            super().__init__()
            self.panel, self.value = panel, value

    def complete(self, value=None):
        self.post_message(self.Resolved(self, value))

    # Keeps existing result-producing handlers readable during migration.
    dismiss = complete

    def set_focus(self, widget):
        widget.focus()

    def apply_theme(self, theme):
        self.styles.background = theme.background
        self.styles.color = theme.foreground
        self.styles.border_top = ('ascii' if theme.glyph_running == '>' else 'solid', theme.foreground if theme.no_color else theme.accent)
        for widget in self.query('*'):
            widget.styles.background = theme.background
            widget.styles.color = theme.foreground
            identifier = widget.id or ''
            if identifier.endswith(('title',)):
                widget.styles.color = theme.accent
            elif identifier.endswith(('keys', 'current', 'help', 'status')):
                widget.styles.color = theme.muted
            elif identifier.endswith(('error',)):
                widget.styles.color = theme.danger
            if isinstance(widget, OptionList):
                if isinstance(widget, ChoiceList):
                    widget.set_semantic_theme(theme)
            elif isinstance(widget, (Input, TextArea)):
                if isinstance(widget, FieldInput):
                    widget.semantic_theme = theme
                widget.styles.background = theme.surface
                # Inputs own their compact/expanded border and height budget.
                if not isinstance(widget, Input):
                    widget.styles.border = ('solid', theme.border)
            elif isinstance(widget, Button):
                widget.styles.border = ('none', theme.border)
                widget.styles.height = 1
                widget.styles.min_height = 1
                widget.styles.max_height = 1
                widget.styles.background = theme.surface
                widget.styles.color = theme.foreground
