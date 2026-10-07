"""Compact approval at the composer, keeping the conversation in view."""
from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ui.presentation import tool_label


def session_rule(action):
    rule = (action.get('_approval') or {}).get('suggested_rule')
    return rule if isinstance(rule, str) and rule.strip() and '<' not in rule else None


def render_approval_content(action, project_root, theme, api_key='', expanded=False):
    args = action.get('args') or {}
    approval = action.get('_approval') or {}
    preview = approval.get('preview') or action.get('preview') or {}
    text = Text()

    def field(label, value, color=None):
        if value is None or value == '':
            return
        value = str(value).replace(api_key, '[密钥已隐藏]') if api_key else str(value)
        text.append(label + '  ', style=None if theme.no_color else theme.muted)
        text.append(value + '\n', style=None if theme.no_color else color)

    if action.get('name') == 'run_command':
        field('$', args.get('command', preview.get('command', '')), theme.foreground)
        field('目录', preview.get('cwd') or args.get('cwd') or project_root)
    else:
        field('文件', preview.get('path') or args.get('path'))
    field('原因', approval.get('reason') or '该操作需要你的允许。')
    diff = preview.get('diff') or preview.get('error')
    if diff:
        if expanded:
            field('变更', diff)
        else:
            field('变更', '\n'.join(str(diff).splitlines()[:3]))
    if expanded:
        field('会话规则', session_rule(action))
        presented = {'command', 'cwd'} if action.get('name') == 'run_command' else {'path'}
        for name, value in args.items():
            if name not in presented:
                field({'content': '写入内容', 'timeout_seconds': '超时秒数',
                       'old_text': '替换前', 'new_text': '替换后'}.get(name, name), value)
    text.rstrip()
    return text


from ui.interaction import ChoiceList


class ApprovalPrompt(Vertical):
    BINDINGS = [Binding('escape,1', 'reject', show=False, priority=True),
                Binding('2', 'approve', show=False, priority=True),
                Binding('3', 'session', show=False, priority=True),
                Binding('d', 'details', show=False, priority=True),
                Binding('pageup', 'scroll_details_up', show=False, priority=True),
                Binding('pagedown', 'scroll_details_down', show=False, priority=True)]
    DEFAULT_CSS = '''
    ApprovalPrompt { height: auto; max-height: 60%; margin: 0 2;
        padding: 0 1; border-top: solid $warning; }
    ApprovalPrompt #approval-title { height: 1; text-style: bold; }
    ApprovalPrompt #approval-body { height: auto; max-height: 4; scrollbar-size-vertical: 1; }
    ApprovalPrompt #approval-details { height: auto; }
    ApprovalPrompt #approval-choices { height: auto; max-height: 3; padding: 0; border: none; }
    ApprovalPrompt #approval-hint { height: 1; }
    ApprovalPrompt.expanded { height: 60%; max-height: 60%; }
    ApprovalPrompt.expanded #approval-body { height: 1fr; max-height: 100%; }
    ApprovalPrompt.compact { margin: 0; }
    '''

    class Decided(Message):
        def __init__(self, decision):
            super().__init__()
            self.decision = decision

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.expanded = False
        self._action = {}
        self._decisions = ['reject', 'approve']

    def compose(self):
        yield Static('', id='approval-title', markup=False)
        with VerticalScroll(id='approval-body'):
            yield Static('', id='approval-details', markup=False)
        yield ChoiceList(id='approval-choices')
        yield Static('', id='approval-hint', markup=False)

    def configure(self, action, index, total, project_root, theme, api_key=''):
        self._action, self._project_root, self._theme, self._api_key = action, project_root, theme, api_key
        self.expanded = False
        self.remove_class('expanded')
        self.styles.background = theme.surface
        self.styles.color = theme.foreground
        self.styles.border_top = ('solid', theme.foreground if theme.no_color else theme.role_user or theme.accent)
        title = f'需要允许 · {tool_label(action.get("name", "unknown"))} · {index}/{total}'
        if api_key:
            title = title.replace(api_key, '[密钥已隐藏]')
        self.query_one('#approval-title', Static).update(Text(title, style=None if theme.no_color else f'bold {theme.role_user or theme.accent}'))
        choices = self.query_one('#approval-choices', OptionList)
        choices.clear_options()
        self._decisions = ['reject', 'approve']
        labels = ['1. 拒绝', '2. 仅本次允许']
        if session_rule(action):
            self._decisions.append('approve_session')
            labels.append('3. 本会话允许')
        choices.add_options([Option(Text(label), id=decision) for label, decision in zip(labels, self._decisions)])
        choices.highlighted = 0
        choices.set_semantic_theme(theme)
        self._refresh_content()

    def focus_choices(self):
        self.query_one('#approval-choices', OptionList).focus()

    def _refresh_content(self):
        self.query_one('#approval-details', Static).update(render_approval_content(
            self._action, self._project_root, self._theme, self._api_key, self.expanded))
        self.query_one('#approval-hint', Static).update(Text(
            ('Up/Down' if self._theme.glyph_running == '>' else '↑↓') +
            ' 选择 · Enter 确认 · Esc 拒绝 · d ' + ('收起 · PgUp/PgDn 滚动' if self.expanded else '完整内容'),
            style=None if self._theme.no_color else self._theme.muted))

    @on(OptionList.OptionSelected, '#approval-choices')
    def choose(self, event):
        event.stop()
        self.post_message(self.Decided(self._decisions[event.option_index]))

    def action_reject(self):
        self.post_message(self.Decided('reject'))

    def action_approve(self):
        self.post_message(self.Decided('approve'))

    def action_session(self):
        if session_rule(self._action):
            self.post_message(self.Decided('approve_session'))

    def action_details(self):
        self.expanded = not self.expanded
        self.set_class(self.expanded, 'expanded')
        self._refresh_content()

    def action_scroll_details_up(self):
        self.query_one('#approval-body', VerticalScroll).scroll_page_up(animate=False)

    def action_scroll_details_down(self):
        self.query_one('#approval-body', VerticalScroll).scroll_page_down(animate=False)
