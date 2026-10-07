"""A transcript that keeps renderables so existing messages reflow on resize."""

from rich.text import Text
from rich.style import Style
from rich.markdown import Markdown
from rich.padding import Padding
from rich.console import Group
from rich.segment import Segment
from rich.cells import cell_len
from textual.selection import Selection
from textual.strip import Strip
from textual.geometry import Size
from rich.measure import measure_renderables
from textual.widgets import RichLog
from ui.presentation import indent, render_role_header


class AssistantTurn:
    """One retained reply: progress, activity and streamed answer share a position."""

    def __init__(self, theme):
        self.theme = theme
        self.text = ''
        self.progress = '正在推理…'
        self.spinner = None
        self.elapsed_seconds = None
        self.activities = []
        self.pending_activity = None
        self.finished = False

    def contains_renderable(self, content):
        return any(item is content for item in self.activities)

    def __rich_console__(self, console, options):
        yield render_role_header('assistant', theme=self.theme)
        for activity in self.activities:
            yield activity
        if self.pending_activity is not None:
            yield indent(self.pending_activity)
        if self.text:
            yield indent(Markdown(self.text, code_theme=self.theme.code_theme,
                style='none' if self.theme.no_color else self.theme.foreground))
        elif self.progress:
            progress = Text(style=None if self.theme.no_color else self.theme.muted)
            if self.spinner:
                progress.append(self.spinner + ' ', style=None if self.theme.no_color else self.theme.accent)
            progress.append(self.progress)
            if self.elapsed_seconds is not None:
                progress.append(f' {self.elapsed_seconds:.1f}s')
            yield indent(progress)


class TranscriptLog(RichLog):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._entries = []
        self._render_size = None
        self._resize_scroll = None
        self._reflow_scheduled = False
        self._follow_requested = False
        self._replies = []
        self._selection_reflow_pending = False
        self._entry_cache = []
        self._dirty_entries = set()
        self._full_reflow_pending = False

    def get_selection(self, selection: Selection):
        # Select the displayed text, including Markdown tables and code, rather
        # than a serialized message or the full reply behind it.
        text = '\n'.join(line.text.rstrip() for line in self.lines)
        if selection.start is not None and selection.start.y >= len(text.splitlines()):
            return '', '\n'
        return selection.extract(text), '\n'

    def render_line(self, y):
        line = super().render_line(y)
        scroll_x, scroll_y = self.scroll_offset
        virtual_y = scroll_y + y
        full_text = self.lines[virtual_y].text if virtual_y < len(self.lines) else ''
        character_offset = 0
        cells = 0
        for character in full_text:
            if cells >= scroll_x:
                break
            cells += cell_len(character)
            character_offset += 1
        selection = self.text_selection
        if selection is not None and (span := selection.get_span(virtual_y)) is not None:
            start, end = span
            start_cell = max(0, cell_len(full_text[:start]) - scroll_x)
            end_cell = line.cell_length if end == -1 else max(0, cell_len(full_text[:end]) - scroll_x)
            start_cell = min(start_cell, line.cell_length)
            end_cell = min(end_cell, line.cell_length)
            if end_cell > start_cell:
                # Apply selection after the Rich/Markdown colors so the
                # existing foreground/background cannot hide the highlight.
                highlight = Strip(Segment.apply_style(line.crop(start_cell, end_cell),
                    post_style=self.screen.get_component_rich_style('screen--selection')))
                line = Strip.join((line.crop(0, start_cell),
                    highlight,
                    line.crop(end_cell)))
        return line.apply_offsets(character_offset, virtual_y)

    def selection_updated(self, selection):
        self.refresh()
        if selection is None and self._selection_reflow_pending:
            self._selection_reflow_pending = False
            if not self._reflow_scheduled:
                self._reflow_scheduled = True
                self.call_after_refresh(self._reflow)

    def register_reply(self, content):
        """Keep raw reply text or a live turn reference, separate from tool rows."""
        self._replies.append(content)

    def reply_texts(self):
        """Take a copy snapshot on demand; streaming does not rebuild this list."""
        return [text for content in self._replies
                if (text := content.text if isinstance(content, AssistantTurn) else content).strip()]

    def write(self, content, width=None, expand=False, shrink=True, scroll_end=None, animate=False):
        # RichLog replays writes deferred before the first layout through this
        # method. Record them only on that replay to avoid duplicate messages.
        if self._size_known:
            self._entries.append((content.copy() if isinstance(content, Text) else content, width, expand, shrink))
            if self.text_selection is not None:
                self._selection_reflow_pending = True
                return self
        follow = self.auto_scroll if scroll_end is None else scroll_end
        if follow:
            self._follow_requested = True
            if self._resize_scroll is not None:
                self._resize_scroll = (True, self.scroll_y)
        line_start = len(self.lines)
        result = super().write(content, width, expand, shrink, scroll_end, animate)
        if self._size_known:
            self._entry_cache.append(tuple(self.lines[line_start:]))
        if follow:
            self.call_after_refresh(self._clear_follow_request)
        return result

    def _clear_follow_request(self):
        self._follow_requested = False

    def clear(self):
        self._selection_reflow_pending = False
        if self.is_mounted and self.text_selection is not None:
            self.screen.clear_selection()
        self._entries.clear()
        self._replies.clear()
        self._entry_cache.clear()
        self._dirty_entries.clear()
        return super().clear()

    def _size_updated(self, size, virtual_size, container_size, layout=True):
        # Capture the position before Textual changes the viewport and clamps
        # its scroll offset. The Resize message arrives after that change.
        if (
            self._render_size is not None
            and (self._size != size or self.container_size != container_size)
            and self._resize_scroll is None
        ):
            self._resize_scroll = (self.is_vertical_scroll_end or self._follow_requested, self.scroll_y)
        return super()._size_updated(size, virtual_size, container_size, layout)

    def on_resize(self, event):
        super().on_resize(event)
        geometry = (event.size, event.container_size)
        if self._render_size is not None and self._render_size != geometry:
            self._full_reflow_pending = True
            if not self._reflow_scheduled:
                self._reflow_scheduled = True
                self.call_after_refresh(self._reflow)
        self._render_size = geometry

    def _reflow(self):
        if self.text_selection is not None:
            self._selection_reflow_pending = True
            self._reflow_scheduled = False
            return
        follow, position = self._resize_scroll or (self.is_vertical_scroll_end, self.scroll_y)
        self._resize_scroll = None
        self._reflow_scheduled = False
        self._full_reflow_pending = False
        self._dirty_entries.clear()
        self._entry_cache.clear()
        super().clear()
        for content, width, expand, shrink in self._entries:
            start = len(self.lines)
            super().write(content, width, expand, shrink, scroll_end=False)
            self._entry_cache.append(tuple(self.lines[start:]))
        if follow:
            # A footer may reflow again before the deferred scroll completes.
            # Preserve following through that second viewport resize.
            self._follow_requested = True
            self.scroll_end(animate=False, x_axis=False)
            self.call_after_refresh(self._clear_follow_request)
        else:
            self.scroll_to(y=position, animate=False)

    def refresh_renderable(self, content):
        """Update a retained activity group without appending transcript lines."""
        for index, entry in enumerate(self._entries):
            if entry[0] is content or (callable(getattr(entry[0], 'contains_renderable', None)) and entry[0].contains_renderable(content)):
                self._dirty_entries.add(index)
        if self._dirty_entries and not self._reflow_scheduled:
            self._reflow_scheduled = True
            self.call_after_refresh(self._refresh_cached)

    def _render_entry(self, entry):
        content, width, expand, shrink = entry
        renderable = self._make_renderable(content)
        console = self.app.console
        options = console.options
        if isinstance(renderable, Text) and not self.wrap:
            options = options.update(overflow='ignore', no_wrap=True)
        if width is None:
            width = measure_renderables(console, options, [renderable]).maximum
            available = self.scrollable_content_region.width
            if expand:
                width = max(width, available)
            if shrink:
                width = min(width, available)
            width = max(width, self.min_width)
        lines = list(Segment.split_lines(console.render(renderable, options.update_width(width))))
        return tuple(Strip.from_lines(lines)) if lines else (Strip.blank(width),)

    def _refresh_cached(self):
        if self.text_selection is not None:
            self._selection_reflow_pending = True
            self._reflow_scheduled = False
            return
        if self._full_reflow_pending or len(self._entry_cache) != len(self._entries):
            self._reflow()
            return
        follow, position = self.is_vertical_scroll_end or self._follow_requested, self.scroll_y
        self._reflow_scheduled = False
        for index in self._dirty_entries:
            self._entry_cache[index] = self._render_entry(self._entries[index])
        self._dirty_entries.clear()
        self.lines = [line for entry in self._entry_cache for line in entry]
        self._line_cache.clear()
        self._widest_line_width = max((line.cell_length for line in self.lines), default=0)
        self.virtual_size = Size(self._widest_line_width, len(self.lines))
        self.refresh()
        if follow:
            self.scroll_end(animate=False, x_axis=False)
        else:
            self.scroll_to(y=position, animate=False)

    def apply_theme(self, old, new):
        """Recolor retained renderables without losing tool click metadata."""
        tokens = ('foreground', 'accent', 'success', 'danger', 'muted', 'role_user',
                  'role_assistant', 'role_error', 'body_user', 'tool_label', 'tool_line')
        colors = {
            Style(color=getattr(old, token)).color: getattr(new, token)
            for token in tokens if getattr(old, token) and getattr(new, token)
        }

        def recolor(style):
            style = Style.parse(style) if isinstance(style, str) else style
            if style and style.color in colors:
                return style + Style(color=colors[style.color])
            return style

        def update(content):
            if isinstance(content, Text):
                content.style = recolor(content.style)
                content.spans = [span._replace(style=recolor(span.style)) for span in content.spans]
            elif isinstance(content, Markdown):
                content.style = 'none' if new.no_color else 'markdown.paragraph'
                content.code_theme = new.code_theme
            elif isinstance(content, Padding):
                update(content.renderable)
            elif isinstance(content, Group):
                for child in content.renderables:
                    update(child)
            elif isinstance(content, AssistantTurn):
                content.theme = new
                for child in content.activities:
                    update(child)
                if content.pending_activity is not None:
                    update(content.pending_activity)
            elif callable(getattr(content, 'apply_theme', None)):
                content.apply_theme(new)

        for content, *_ in self._entries:
            update(content)
        self._reflow()
