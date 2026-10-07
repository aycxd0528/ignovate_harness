"""Compact model choices at the foot of the conversation."""
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from ui.interaction import InteractionPanel, ChoiceList, FieldInput
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from nailong.core.preferences import validate_new_model
from ui.model_flow import ModelChoice


class ModelScreen(InteractionPanel):
    conversation_panel = True
    BINDINGS = [Binding('escape', 'cancel', show=False, priority=True)]
    DEFAULT_CSS = """
    #model-dialog { width: 100%; height: 100%; padding: 0; }
    #model-title { height: 1; color: $accent; text-style: bold; }
    #model-current { height: 1; color: $text-muted; }
    #model-menu { height: 1fr; }
    #model-options { height: 1fr; border: none; padding: 0; background: transparent; }
    #model-options:focus { border: none; background-tint: transparent; }
    #model-options .option-list--option-highlighted { color: $success; background: $foreground 8%; text-style: bold; }
    #model-options .option-list--option { padding: 0; }
    #model-fields { height: 1fr; }
    #model-form { display: none; height: 1fr; }
    #model-dialog.adding #model-form { height: 1fr; }
    #model-form Static { height: auto; }
    #model-form Input { margin-bottom: 1; }
    #model-error { height: auto; max-height: 2; color: $error; }
    .model-buttons { height: 1; align-horizontal: left; margin-top: 0; }
    .model-buttons Button { height: 1; width: auto; min-width: 0; margin-right: 2; padding: 0 1; border: none; }
    #model-keys { height: 1; color: $text-muted; margin-top: 0; }
    """

    def __init__(self, preferences, *, add_only=False, global_scope=False):
        super().__init__()
        self.preferences = preferences
        self.add_only = add_only
        self.global_scope = global_scope
        self.names = list(preferences['models'])
        self.preferred_height = min(14, len(self.names)+5)

    def compose(self) -> ComposeResult:
        with Vertical(id='model-dialog'):
            yield Static('选择模型', id='model-title', markup=False)
            yield Static(f"当前：{self.preferences['model_name']} · {self.preferences['model']}",
                         id='model-current', markup=False)
            with Vertical(id='model-menu'):
                options = []
                for name, definition in self.preferences['models'].items():
                    marker = '  当前' if name == self.preferences['model_name'] else ''
                    options.append(Option(Text(f"{name}  ·  {definition['model']}{marker}")))
                menu = ChoiceList(*options, id='model-options')
                menu.display = bool(self.names)
                yield menu
                with Horizontal(classes='model-buttons'):
                    yield Button('取消', id='model-cancel', compact=True, flat=True)
                    yield Button('新增模型', id='model-add', compact=True, flat=True)
                    yield Button('保存为默认并切换', id='model-use', compact=True, flat=True, disabled=not self.names)
                yield Static(('用户默认' if self.global_scope else '项目默认') + ' · ↑↓ 选择 · Enter 保存并切换 · Esc 返回', id='model-keys', markup=False)
            with Vertical(id='model-form'):
                scope = '用户偏好' if self.global_scope else '当前项目'
                with VerticalScroll(id='model-fields'):
                    yield Static(f'保存为{scope}默认；沿用当前 API 连接。', markup=False)
                    yield Static('模型名称', markup=False)
                    yield FieldInput(placeholder='例如 pro', id='model-name')
                    yield Static('模型 ID', markup=False)
                    yield FieldInput(placeholder='填写 API 支持的模型 ID', id='model-id')
                with Horizontal(classes='model-buttons'):
                    yield Button('取消', id='model-form-cancel', compact=True, flat=True)
                    yield Button('保存并切换', id='model-save', compact=True, flat=True)
            error = Static('', id='model-error', markup=False)
            error.display = False
            yield error

    def on_mount(self):
        theme = getattr(self.app, '_ui_theme', None)
        if theme is not None:
            self.apply_theme(theme)
        for field in self.query(Input):
            field.styles.height = 1
            field.styles.border = ('none', theme.border if theme is not None else 'gray')
        options = self.query_one('#model-options', OptionList)
        selected = self.preferences['model_name']
        options.highlighted = self.names.index(selected) if selected in self.names else 0
        if self.add_only:
            self.show_form()
        else:
            (options if self.names else self.query_one('#model-add')).focus()

    def show_form(self):
        self.preferred_height = 14
        if hasattr(self.app, '_size_interaction'):
            self.app._size_interaction()
        self.query_one('#model-dialog').add_class('adding')
        self.query_one('#model-menu').display = False
        self.query_one('#model-form').display = True
        self.query_one('#model-name', Input).focus()

    def show_error(self, message):
        self.query_one('#model-error', Static).update('保存失败：' + message)
        self.query_one('#model-error').display = True
        self.query_one('#model-save' if self.query_one('#model-form').display else '#model-options').focus()

    def select_model(self):
        index = self.query_one('#model-options', OptionList).highlighted
        if index is not None:
            self.dismiss(ModelChoice(self.names[index]))

    def save_model(self):
        try:
            name, model_id = validate_new_model(self.query_one('#model-name', Input).value,
                                                self.query_one('#model-id', Input).value,
                                                self.preferences['models'])
        except ValueError as error:
            self.query_one('#model-error', Static).update(str(error))
            self.query_one('#model-error').display = True
            name_input = self.query_one('#model-name', Input)
            invalid_name = not name_input.value.strip() or name_input.value.strip() in self.preferences['models'] or any(c in name_input.value for c in '\r\n')
            (name_input if invalid_name else self.query_one('#model-id', Input)).focus()
            return
        self.dismiss(ModelChoice(name, model_id))

    @on(Button.Pressed)
    def button_pressed(self, event):
        event.stop()
        if event.button.id == 'model-add':
            self.show_form()
        elif event.button.id == 'model-use':
            self.select_model()
        elif event.button.id == 'model-save':
            self.save_model()
        else:
            self.action_cancel()

    @on(OptionList.OptionSelected)
    def option_selected(self, event):
        event.stop()
        self.select_model()

    @on(Input.Submitted)
    def input_submitted(self, event):
        event.stop()
        if event.input.id == 'model-name':
            self.query_one('#model-id', Input).focus()
        else:
            self.save_model()

    def action_cancel(self):
        if self.query_one('#model-form').display and not self.add_only:
            self.query_one('#model-error').display = False
            self.query_one('#model-form').display = False
            self.query_one('#model-menu').display = True
            self.query_one('#model-dialog').remove_class('adding')
            self.preferred_height = min(14, len(self.names)+5)
            if hasattr(self.app, '_size_interaction'):
                self.app._size_interaction()
            self.query_one('#model-options', OptionList).focus()
        else:
            self.dismiss(None)
