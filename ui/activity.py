"""Retained, expandable tool activity for a conversation phase."""
from collections import Counter

from rich.style import Style
from rich.text import Text

from ui.presentation import indent, render_tool_details, render_tool_line, render_tool_rows
from ui.theme import Theme


class ToolGroup:
    def __init__(self, group_id: int, theme: Theme, api_key: str = '', *, interactive=True):
        self.group_id = group_id
        self.theme = theme
        self.api_key = api_key
        self.entries = []
        self.expanded = False
        self.interactive = interactive
        self.detail_indices = set()

    def add(self, index: int, data: dict):
        self.entries.append((index, data))

    def apply_theme(self, theme):
        self.theme = theme

    def summary(self, width=80):
        ascii_only = self.theme.glyph_running == '>'
        marker = ('v' if self.expanded else '>') if ascii_only else ('▾' if self.expanded else '▸')
        row = Text(marker + ' ', style=None if self.theme.no_color else self.theme.muted)
        if len(self.entries) == 1:
            row.append_text(render_tool_line(self.entries[0][1], width=max(1, width - 2), theme=self.theme, api_key=self.api_key))
        else:
            names = {'read_file': '读取', 'list_files': '目录', 'find_files': '查找', 'glob': '查找', 'grep': '搜索',
                     'search_files': '搜索', 'search_text': '搜索', 'run_command': '命令',
                     'write_file': '写入', 'edit_file': '修改'}
            counts = Counter(names.get(data.get('name'), '其他') for _, data in self.entries)
            row.append(' · '.join(f'{name} {count}' for name, count in counts.items()))
            elapsed = sum(data.get('elapsed_ms', 0) for _, data in self.entries
                          if isinstance(data.get('elapsed_ms'), (int, float)))
            if elapsed:
                row.append(f' · {elapsed:.0f}ms' if elapsed < 1000 else f' · {elapsed / 1000:.1f}s')
            failures = sum(self.failed(data) for _, data in self.entries)
            if failures:
                row.append(f' · {failures} 项未通过', style=None if self.theme.no_color else self.theme.danger)
            row.append(('  Ctrl+O 展开' if not self.expanded else '  Ctrl+O 折叠') if self.interactive else '  /tools 查看详情')
            row.truncate(max(1, width), overflow='ellipsis')
        row.stylize(Style(meta={'nailong_group': self.group_id}))
        return row

    @staticmethod
    def failed(data):
        return data.get('ok') is False or bool(data.get('timed_out')) or data.get('exit_code') not in (None, 0)

    def __rich_console__(self, console, options):
        width = max(1, options.max_width - 2)
        yield indent(self.summary(width))
        entries = self.entries if self.expanded else [
            entry for entry in self.entries if self.failed(entry[1])
        ]
        for position, (index, data) in enumerate(entries):
            rows = [render_tool_details(data, theme=self.theme, api_key=self.api_key)] if index in self.detail_indices else (
                render_tool_rows(data, width=max(1, width - 2), theme=self.theme,
                                 api_key=self.api_key, output_style='normal'))
            if not self.expanded and position < len(entries) - 1:
                rows = rows[:1]
            for row in rows:
                row.stylize(Style(meta={'nailong_tool': index}))
                yield indent(row, 4)
