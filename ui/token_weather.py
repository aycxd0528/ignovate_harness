"""One compact weather row for the last measured main-model context."""
from rich.text import Text

from ui.theme import load_theme


def _tokens(value):
    if value >= 1_000_000:
        return f'{value / 1_000_000:.1f}'.rstrip('0').rstrip('.') + 'm'
    if value >= 1_000:
        return f'{value / 1_000:.1f}'.rstrip('0').rstrip('.') + 'k'
    return str(value)


def render_token_weather(metrics, *, context_window, width=100, theme=None, show_empty=False):
    """Fit weather, occupancy, turn history and growth without inventing usage.

    Samples are the last actual input_tokens of each visible turn, including
    tool definitions and results in that request. Child usage and cumulative
    session billing are deliberately separate from this context measurement.
    """
    theme = theme or load_theme()
    width = max(0, width)
    current = metrics.last_input_tokens
    known = type(current) is int and current >= 0
    window_known = type(context_window) is int and context_window > 0
    ascii_only = theme.glyph_running == '>'
    separator = ' | ' if ascii_only else ' · '
    muted = None if theme.no_color else theme.muted
    if not known and not metrics.turns and not metrics.context_history and metrics.usage_complete:
        if not show_empty:
            return Text('')
        color = ('yellow' if theme.name == 'ansi' else
                 '#936c00' if theme.name == 'light' else '#e3b341')
        row = Text(no_wrap=True, overflow='crop')
        row.append(('' if ascii_only else '☀ ') + 'Clear',
                   style=None if theme.no_color else f'bold {color}')
        row.append(separator + '0%' + separator +
                   (f'0 / {_tokens(context_window)}' if window_known else
                    '0 / Unknown window' if ascii_only else '0 / 未知窗口'), style=muted)
        row.truncate(width, overflow='crop')
        return row
    if not known or not window_known:
        if not known:
            message = '未知用量' if metrics.context_history or not metrics.usage_complete else '待采样'
            occupancy = f'— / {_tokens(context_window)}' if window_known else '未知窗口'
        else:
            message, occupancy = '未知窗口', f'{_tokens(current)} / 未知窗口'
        if ascii_only:
            message = {'未知用量': 'Unknown usage', '待采样': 'Waiting for usage', '未知窗口': 'Unknown window'}[message]
            occupancy = occupancy.replace('—', '-').replace('未知窗口', 'Unknown window')
        row = Text(separator.join([message, occupancy]), style=muted)
        row.truncate(width, overflow='crop')
        return row

    percent = round(current / context_window * 100)
    label, icon, dark, light, ansi = (
        ('Clear', '☀', '#e3b341', '#936c00', 'yellow') if percent < 30 else
        ('Cloudy', '☁', '#a7b3c2', '#576475', 'bright_black') if percent < 50 else
        ('Showers', '☂', '#58a6ff', '#0969da', 'cyan') if percent < 80 else
        ('Storm', '⚡', '#f778ba', '#9f2976', 'magenta')
    )
    color = ansi if theme.name == 'ansi' else light if theme.name == 'light' else dark
    weather_style = None if theme.no_color else f'bold {color}'
    percent_style = None if theme.no_color else f'bold {theme.foreground}'
    history = metrics.context_history or [current]
    previous = history[-2] if len(history) > 1 else 0
    delta = current - previous if previous is not None else None
    direction = ('^' if delta > 0 else 'v' if delta < 0 else '=') if ascii_only and delta is not None else (
        '▲' if delta and delta > 0 else '▼' if delta and delta < 0 else '=')
    growth = f'{direction} {"+" if delta >= 0 else "-"}{_tokens(abs(delta))}' if delta is not None else '增量待统计'
    if ascii_only and delta is None:
        growth = 'Growth unavailable'
    levels = '.:-=+*#@' if ascii_only else '▁▂▃▄▅▆▇█'
    bars = ''.join(('-' if ascii_only else '·') if sample is None else levels[min(7, max(0, int(sample / context_window * 8)))]
                   for sample in history[-8:])

    def assemble(*, full=False, show_bars=True, show_growth=True, show_numbers=True):
        row = Text()
        row.append(('' if ascii_only else icon + ' ') + label + ' ', style=weather_style)
        row.append(f'{percent}%', style=percent_style)
        if full:
            row.append(' of context', style=muted)
        if show_numbers:
            row.append(separator + f'{_tokens(current)} / {_tokens(context_window)}', style=muted)
        if show_bars:
            row.append(separator + ('last turns ' if full else ''), style=muted)
            row.append(bars, style=weather_style)
        if show_growth:
            row.append(separator + growth + (' last turn' if full and delta is not None else ''), style=muted)
        return row

    from rich.cells import cell_len
    for options in [dict(full=True), {}, dict(show_bars=False),
                    dict(show_bars=False, show_growth=False),
                    dict(show_bars=False, show_growth=False, show_numbers=False)]:
        row = assemble(**options)
        if cell_len(row.plain) <= width:
            return row
    row.truncate(width, overflow='crop')
    return row
