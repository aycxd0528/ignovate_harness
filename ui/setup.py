"""Keyboard-first welcome and model setup before Agent construction."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from config import Settings, validate_connection
from nailong.core.bootstrap import BootstrapStore
from nailong.core.branding import PRODUCT_NAME
from nailong.core.reasoning import PERMISSION_OPTIONS, REASONING_LABELS, reasoning_options
from ui.wordmark import welcome_wordmark
from ui.theme import load_theme
from ui.interaction import InteractionPanel, ChoiceList, FieldInput


@dataclass(frozen=True)
class SetupResult:
    settings: Settings = field(repr=False)
    permission_mode: str = 'default'


class SetupChoices(ChoiceList):
    """Terminal rows with a cursor and no filled selection box."""
    def __init__(self, choices, current=None, **kwargs):
        self.choices = list(choices)
        self.current = current
        super().__init__(*[Option('', id=key) for key, _, _ in self.choices], **kwargs)
        self.highlighted = next((i for i, row in enumerate(self.choices) if row[0] == current), 0)
        self._paint_rows()

    @property
    def value(self):
        index = self.highlighted
        return self.choices[index][0] if index is not None else self.current

    def replace_choices(self, choices):
        previous = self.value
        self.choices = list(choices)
        self.clear_options().add_options(Option('', id=key) for key, _, _ in self.choices)
        self.highlighted = next((i for i, row in enumerate(self.choices) if row[0] == previous), 0)
        self._paint_rows()

    def _paint_rows(self):
        label_width = max((Text(label).cell_len for _, label, _ in self.choices), default=0)
        for index, (key, label, description) in enumerate(self.choices):
            selected = index == self.highlighted
            theme = getattr(self.app, '_ui_theme', load_theme()) if self.is_mounted else load_theme()
            cursor = '> ' if theme.glyph_running == '>' else '❯ '
            row = Text(cursor if selected else '  ')
            row.append(label, style='bold' if theme.no_color or selected else theme.foreground)
            if key == self.current:
                row.append(' *' if theme.glyph_running == '>' else ' ✓', style=None if theme.no_color else theme.success)
            if description:
                row.append(' ' * (label_width - Text(label).cell_len + 3))
                row.append(description, style=None if theme.no_color else theme.muted)
            self.replace_option_prompt_at_index(index, row)

    def on_option_list_option_highlighted(self, event):
        if event.option_list is self:
            self._paint_rows()
            event.stop()


class SetupApp(App[SetupResult | None]):
    TITLE = PRODUCT_NAME
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding('escape', 'back', priority=True, show=False),
                Binding('ctrl+c', 'cancel', priority=True, show=False)]
    CSS = """
    Screen { background: #1d2233; color: #d5dbea; align: left top; }
    #setup-shell { width: 100%; height: 100%; padding: 0 2; }
    #setup-topbar { height: 1; margin-bottom: 1; }
    #setup-brand { width: auto; text-style: bold; }
    #setup-session { width: 1fr; }
    #setup-status { width: auto; }
    #setup-actions { height: 1; margin-top: 1; }
    #setup-composer { height: 3; border: round $accent; padding: 0 1; }
    .field-error { height: auto; }
    #setup-logo { height: auto; color: #82b4fa; }
    #welcome-title { height: 1; color: #82b4fa; text-style: bold; }
    #setup-caption { height: auto; color: #a0abc0; margin-bottom: 1; }
    #setup-rule { height: 1; border-top: solid #626d85; }
    #setup-body { height: 1fr; max-width: 108; }
    #setup-title { height: auto; color: #82b4fa; text-style: bold; margin-bottom: 1; }
    .setup-step { height: auto; }
    #welcome-copy, #setup-project { height: auto; margin-bottom: 1; }
    #setup-project { color: #a0abc0; }
    #setup-fields { height: auto; }
    .field-label { height: 1; color: #a0abc0; }
    Input { height: 1; border: none; margin-bottom: 1; padding: 0 1;
        background: #252c40; color: #d5dbea; }
    Input:focus { border: none; background: #303c55; }
    .semantic-light Input>.input--placeholder { color: #596671; }
    .semantic-dark Input>.input--placeholder { color: #8c9aa8; }
    .setup-buttons Button:focus { text-style: bold reverse; }
    SetupChoices { height: auto; border: none; padding: 0; background: transparent; }
    SetupChoices:focus { border: none; background-tint: transparent; }
    SetupChoices .option-list--option { padding: 0; }
    SetupChoices .option-list--option-highlighted { background: transparent; }
    SetupChoices .option-list--option-hover-highlighted { background: transparent; }
    #setup-reasoning, #setup-permissions { margin-bottom: 1; }
    #setup-summary, #setup-note { height: auto; color: #a0abc0; margin-bottom: 1; }
    #setup-error { height: auto; color: #efa0a0; margin-top: 1; }
    #setup-keys { height: 1; color: #8e97ae; margin-top: 0; }
    .setup-buttons { height: 1; margin-top: 1; }
    .setup-buttons Button { height: 1; min-width: 0; width: auto; padding: 0 1;
        border: none; background: transparent; color: #a0abc0; margin-right: 3; }
    .setup-buttons Button:focus { background: transparent; color: #82b4fa; text-style: bold; }
    .setup-buttons Button:hover { background: #303c55; }
    """

    def __init__(self, *, store=None, settings=None, project_root=None, permission_mode='default',
                 locked_permission=False, locked_reasoning=None, theme=None):
        super().__init__()
        self._ui_theme = theme or load_theme()
        self.store = store or BootstrapStore()
        self.settings = settings
        self.project_root = Path(project_root or (settings.project_root if settings else Path.cwd()))
        existing = self.store.read().get('provider', {})
        self.base = settings.api_base if settings else existing.get('api_base', os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'))
        self.model = settings.model if settings else existing.get('model', os.getenv('DEEPSEEK_MODEL', 'deepseek-flash'))
        self.existing_key = settings.api_key if settings else existing.get('api_key', os.getenv('DEEPSEEK_API_KEY', ''))
        self.permission_mode = permission_mode
        self.locked_permission = locked_permission
        self.reasoning = locked_reasoning or (settings.reasoning_effort if settings else 'default')
        self.locked_reasoning = locked_reasoning
        self.step = 0

    def compose(self) -> ComposeResult:
        with Vertical(id='setup-shell'):
            with Horizontal(id='setup-topbar'):
                yield Static(PRODUCT_NAME, id='setup-brand', markup=False)
                yield Static(' / ' + self.project_root.name, id='setup-session', markup=False)
                yield Static('配置', id='setup-status', markup=False)
            yield Static('', id='setup-logo')
            yield Static(f'{PRODUCT_NAME}  v1.0.0', id='welcome-title', markup=False)
            yield Static('读代码，改文件，让任务有始有终。', id='setup-caption')
            yield Static('', id='setup-rule')
            with VerticalScroll(id='setup-body'):
                yield Static('开始使用', id='setup-title')
                with Vertical(id='welcome', classes='setup-step'):
                    yield Static('先连接你的模型，再选择推理强度和本次权限。', id='welcome-copy')
                    yield Static(f'工作目录  {self.project_root}', id='setup-project', markup=False)
                    yield SetupChoices((('next', '配置并开始', ''), ('cancel', '退出', '')),
                                       current='next', id='welcome-options')
                with Vertical(id='setup-connection', classes='setup-step'):
                    with Vertical(id='setup-fields'):
                        yield Static('API 地址', classes='field-label')
                        yield FieldInput(value=self.base, id='setup-base')
                        yield Static('', id='setup-base-error', classes='field-error', markup=False)
                        yield Static('模型 ID', classes='field-label')
                        yield FieldInput(value=self.model, id='setup-model')
                        yield Static('', id='setup-model-error', classes='field-error', markup=False)
                        yield Static('API Key', classes='field-label')
                        yield FieldInput(password=True, placeholder='已配置，留空保留' if self.existing_key else '输入你的 API Key', id='setup-key')
                        yield Static('', id='setup-key-error', classes='field-error', markup=False)
                    yield Static('填写模型服务接口地址；密钥隐藏输入，确认前不会保存。', id='setup-connection-help')
                with Vertical(id='setup-reasoning-step', classes='setup-step'):
                    yield SetupChoices(reasoning_options(self.model), current=self.reasoning,
                                       disabled=bool(self.locked_reasoning), id='setup-reasoning')
                    yield Static('根据任务选择推理深度，进入会话后可用 /reasoning 调整。', id='setup-reasoning-help')
                with Vertical(id='setup-permissions-step', classes='setup-step'):
                    yield Static('', id='setup-summary', markup=False)
                    choices = list(PERMISSION_OPTIONS)
                    if self.permission_mode == 'plan': choices.append(('plan', '计划只读', '只允许只读工具'))
                    yield SetupChoices(choices, current=self.permission_mode,
                                       disabled=self.locked_permission, id='setup-permissions')
                    yield Static('连接配置保存到本机；权限仅本次启动生效。', id='setup-note')
            yield Static('', id='setup-error', markup=False)
            with Horizontal(id='setup-actions', classes='setup-buttons'):
                yield Button('继续', id='setup-next', compact=True, flat=True)
                yield Button('继续', id='reasoning-next', compact=True, flat=True)
                yield Button('保存并开始', id='setup-start', compact=True, flat=True)
                yield Button('返回', id='setup-back', compact=True, flat=True)
            with Vertical(id='setup-composer'):
                yield Static('', id='setup-keys', markup=False)

    def on_mount(self):
        self.add_class('semantic-light' if self._ui_theme.name == 'light' else 'semantic-dark')
        self.theme = 'textual-light' if self._ui_theme.name == 'light' else 'textual-dark'
        self.screen.styles.background = self._ui_theme.background
        self.screen.styles.color = self._ui_theme.foreground
        InteractionPanel.apply_theme(self, self._ui_theme)
        self.query_one('#setup-composer').styles.border = ('round', self._ui_theme.border)
        self.query_one('#setup-rule').styles.border_top = ('solid', self._ui_theme.border)
        self.screen.styles.border = ('none', self._ui_theme.background)
        self.styles.border = ('none', self._ui_theme.background)
        for widget in self.query('.field-error'):
            widget.styles.color = self._ui_theme.danger
            widget.display = False
        for identifier in ('setup-caption', 'setup-session', 'setup-project', 'setup-note', 'setup-summary', 'setup-keys'):
            self.query_one('#'+identifier).styles.color = self._ui_theme.muted
        for widget in self.query('Input'):
            widget.styles.height = 1
            widget.styles.border = ('none', self._ui_theme.border)
            widget.get_component_styles('input--placeholder').color = self._ui_theme.muted
        for widget in self.query(SetupChoices):
            widget._paint_rows()
        self.update_reasoning_options()
        self.show_step(0)

    def on_resize(self, event):
        if self.is_mounted:
            self.update_brand(size=event.size)

    def update_brand(self, *, size=None):
        size = size or self.size
        logo = self.query_one('#setup-logo', Static)
        art = welcome_wordmark(width=max(0, size.width-4), height=size.height, theme=self._ui_theme)
        logo.display = self.step == 0 and art is not None
        logo.update(art or '')
        self.query_one('#setup-caption').display = self.step == 0

    def show_step(self, step):
        self.step = step
        for index, selector in enumerate(('#welcome', '#setup-connection', '#setup-reasoning-step', '#setup-permissions-step')):
            self.query_one(selector).display = index == step
        self.query_one('#setup-title', Static).update(('开始使用', '1 / 3  连接模型', '2 / 3  推理强度', '3 / 3  本次权限')[step])
        self.query_one('#setup-error').display = False
        self.query_one('#setup-actions').display = step != 0
        self.query_one('#setup-next').display = step == 1
        self.query_one('#reasoning-next').display = step == 2
        self.query_one('#setup-start').display = step == 3
        self.query_one('#setup-back').display = step != 0
        for widget in self.query('.field-error'):
            widget.display = False
        self.query_one('#setup-keys', Static).update(
            '↑↓ 选择 · Enter 开始 · Esc 退出' if step == 0 else
            'Tab 切换 · Enter 继续 · Esc 返回' if step == 1 else
            'Enter 继续 · Esc 返回' if step == 2 and self.locked_reasoning else
            'Enter 保存并开始 · Esc 返回' if step == 3 and self.locked_permission else
            '↑↓ 选择 · Enter '+('保存并开始' if step == 3 else '继续')+' · Esc 返回')
        self.update_brand()
        self.query_one('#setup-body', VerticalScroll).scroll_home(animate=False)
        focus = ('#welcome-options', '#setup-base',
                 '#reasoning-next' if self.locked_reasoning else '#setup-reasoning',
                 '#setup-start' if self.locked_permission else '#setup-permissions')[step]
        if step == 3:
            effort = self.query_one('#setup-reasoning', SetupChoices).value
            self.query_one('#setup-summary', Static).update(
                f"模型  {self.query_one('#setup-model', Input).value.strip()}\n推理  {REASONING_LABELS[effort]}")
        self.query_one(focus).focus()

    @on(Input.Changed, '#setup-model')
    def model_changed(self, event):
        if self.is_mounted:
            self.update_reasoning_options()

    def update_reasoning_options(self):
        from textual.css.query import NoMatches
        try:
            model = self.query_one('#setup-model', Input).value.strip()
            menu = self.query_one('#setup-reasoning', SetupChoices)
        except NoMatches:
            return
        choices = list(reasoning_options(model))
        if self.locked_reasoning:
            selected = next((row for row in choices if row[0] == self.locked_reasoning),
                            (self.locked_reasoning, REASONING_LABELS[self.locked_reasoning], '当前模型的参数尚未确认'))
            choices = [(selected[0], selected[1], '启动参数已指定；'+selected[2])]
        menu.replace_choices(choices)

    def connection_values(self):
        validate_connection(self.query_one('#setup-base', Input).value, 'valid-model', 'validation')
        validate_connection(self.query_one('#setup-base', Input).value, self.query_one('#setup-model', Input).value, 'validation')
        return validate_connection(self.query_one('#setup-base', Input).value,
                                   self.query_one('#setup-model', Input).value,
                                   self.query_one('#setup-key', Input).value.strip() or self.existing_key)

    def next_step(self):
        try:
            if self.step == 1:
                self.connection_values()
            if self.step == 2:
                from nailong.core.reasoning import model_reasoning_kwargs
                model_reasoning_kwargs(self.query_one('#setup-model', Input).value.strip(),
                                       self.query_one('#setup-reasoning', SetupChoices).value)
            self.show_step(self.step+1)
        except ValueError as error:
            self.show_error(error)

    def show_error(self, error):
        text = str(error)
        field = next((name for token,name in (('DEEPSEEK_BASE_URL','base'), ('DEEPSEEK_MODEL','model'), ('DEEPSEEK_API_KEY','key')) if token in text), None)
        for token,label in (('DEEPSEEK_BASE_URL','API 地址'), ('DEEPSEEK_MODEL','模型 ID'), ('DEEPSEEK_API_KEY','API Key')):
            text = text.replace(token, label)
        if field:
            self.show_step(1)
            widget = self.query_one('#setup-'+field+'-error', Static)
            self.query_one('#setup-'+field, Input).focus()
        else:
            widget = self.query_one('#setup-error', Static)
        widget.update(text)
        widget.display = True
        widget.scroll_visible(animate=False)

    @on(OptionList.OptionSelected)
    def option_selected(self, event):
        event.stop()
        if self.step == 0 and event.option_list.id == 'welcome-options':
            self.show_step(1) if event.option.id == 'next' else self.action_cancel()
        elif self.step == 2 and event.option_list.id == 'setup-reasoning':
            self.next_step()
        elif self.step == 3 and event.option_list.id == 'setup-permissions':
            self.save()

    @on(Input.Submitted)
    def input_submitted(self, event):
        event.stop()
        if self.step != 1:
            return
        next_input = {'setup-base':'#setup-model', 'setup-model':'#setup-key'}.get(event.input.id)
        self.query_one(next_input).focus() if next_input else self.next_step()

    @on(Button.Pressed)
    def button_pressed(self, event):
        event.stop()
        if (self.step == 1 and event.button.id == 'setup-next') or (
                self.step == 2 and event.button.id == 'reasoning-next'):
            self.next_step()
        elif self.step == 3 and event.button.id == 'setup-start':
            self.save()
        elif event.button.id == 'setup-back':
            self.action_back()

    def save(self):
        try:
            base, model, key = self.connection_values()
            effort = self.query_one('#setup-reasoning', SetupChoices).value
            value = self.store.save(base, model, key, effort)
            provider = value['provider']
            settings = Settings(provider['api_key'], provider['api_base'], provider['model'],
                                self.project_root, reasoning_effort=effort)
            self.exit(SetupResult(settings, self.query_one('#setup-permissions', SetupChoices).value))
        except (ValueError, OSError) as error:
            self.show_error(error)

    def action_back(self):
        self.show_step(self.step-1) if self.step else self.action_cancel()

    def action_cancel(self):
        self.exit(None)


def run_setup(*, settings=None, project_root=None, permission_mode='default', selected_ui='plain',
              locked_permission=False, locked_reasoning=None, store=None, theme=None):
    store = store or BootstrapStore()
    if selected_ui == 'textual':
        return SetupApp(store=store, settings=settings, project_root=project_root,
                        permission_mode=permission_mode, locked_permission=locked_permission,
                        locked_reasoning=locked_reasoning, theme=theme).run()
    from getpass import getpass
    from nailong.core.reasoning import reasoning_options
    print(f'欢迎使用 {PRODUCT_NAME}\n本地编码、持续任务与证据交付。配置过程不发送模型请求。')
    if input('Enter 配置并开始；输入 q 取消：').strip().lower() in {'q','quit'}:
        return None
    original = store.read().get('provider', {})
    base = settings.api_base if settings else original.get('api_base', os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'))
    model = settings.model if settings else original.get('model', os.getenv('DEEPSEEK_MODEL', 'deepseek-flash'))
    key = settings.api_key if settings else original.get('api_key', os.getenv('DEEPSEEK_API_KEY', ''))
    print('API 地址应为服务接口。API Key 隐藏输入；已有密钥留空保留。\n连接配置保存到 ~/.ignovate/config.json，权限 0600。')
    base = input(f'API 地址 [{base}]：').strip() or base
    model = input(f'模型 ID [{model}]：').strip() or model
    new_key = getpass('API Key：')
    from ui.settings_flow import prompt_setting_choice_sync
    effort = locked_reasoning or prompt_setting_choice_sync('推理强度',
        settings.reasoning_effort if settings else 'default', reasoning_options(model))
    permission = permission_mode if locked_permission else prompt_setting_choice_sync('权限', permission_mode, PERMISSION_OPTIONS)
    if effort is None or permission is None:
        return None
    value = store.save(base, model, new_key, effort, existing_key=key)
    provider = value['provider']
    return SetupResult(Settings(provider['api_key'], provider['api_base'], provider['model'],
                       Path(project_root or (settings.project_root if settings else Path.cwd())), reasoning_effort=effort), permission)
