"""Solid terminal lettering with an offset double-line contour."""
from __future__ import annotations

from functools import lru_cache
from rich.text import Text
from ui.banner import BANNER_ROWS


# Two-column vertical strokes balance one-row horizontal strokes in terminal cells.
_LETTERS = (
    ('██', '██', '██', '██', '██', '██', '██'),  # I
    (' █████ ', '██   ██', '██     ', '██ ████', '██   ██', '██   ██', ' █████ '),  # G
    ('██   ██', '███  ██', '████ ██', '██ ████', '██  ███', '██   ██', '██   ██'),  # N
    (' █████ ', '██   ██', '██   ██', '██   ██', '██   ██', '██   ██', ' █████ '),  # O
    ('██   ██', '██   ██', '██   ██', '██   ██', ' ██ ██ ', ' █████ ', '  ███  '),  # V
    (' █████ ', '██   ██', '██   ██', '███████', '██   ██', '██   ██', '██   ██'),  # A
    ('███████', '  ███  ', '  ███  ', '  ███  ', '  ███  ', '  ███  ', '  ███  '),  # T
    ('███████', '██     ', '██     ', '██████ ', '██     ', '██     ', '███████'),  # E
)
_LARGE_ROWS = tuple('   '.join(letter[row] for letter in _LETTERS) for row in range(7))

# Connection bits at contour vertices: north, east, south, west.
_LINES = {1:'║', 2:'═', 4:'║', 8:'═', 5:'║', 10:'═',
          6:'╔', 12:'╗', 3:'╚', 9:'╝', 7:'╠', 13:'╣', 14:'╦', 11:'╩', 15:'╬'}


@lru_cache(maxsize=2)
def _contoured_rows(compact: bool) -> tuple[str, ...]:
    rows = BANNER_ROWS if compact else _LARGE_ROWS
    face = {(x,y) for y,row in enumerate(rows) for x,char in enumerate(row) if char == '█'}
    # Box-drawing strokes sit at cell centers: this yields a half-row drop,
    # keeping the extrusion close to the solid face rather than a row away.
    shadow = {(x+1,y) for x,y in face}
    vertices: dict[tuple[int,int], int] = {}

    def edge(start, end, forward, backward):
        vertices[start] = vertices.get(start,0) | forward
        vertices[end] = vertices.get(end,0) | backward

    # Trace both outside contours and counters (the holes within G/O/A).
    for x,y in shadow:
        if (x,y-1) not in shadow:
            edge((x,y),(x+1,y),2,8)
        if (x,y+1) not in shadow:
            edge((x,y+1),(x+1,y+1),2,8)
        if (x-1,y) not in shadow:
            edge((x,y),(x,y+1),4,1)
        if (x+1,y) not in shadow:
            edge((x+1,y),(x+1,y+1),4,1)
    width = max(x for x,_ in vertices)+1
    height = max(y for _,y in vertices)+1
    return tuple(''.join('█' if (x,y) in face else _LINES.get(vertices.get((x,y),0),' ')
                         for x in range(width)) for y in range(height))


def welcome_wordmark(*, width: int, height: int, theme=None) -> Text | None:
    """Fit real terminal columns; retain room for the welcome actions below."""
    if height < 24:
        return None
    full = _contoured_rows(False)
    compact = width < len(full[0]) or height < 30
    rows = _contoured_rows(compact)
    if width < len(rows[0]):
        return None
    art = Text(no_wrap=True, overflow='crop')
    for y,row in enumerate(rows):
        if y:
            art.append('\n')
        for char in row:
            art.append(char, style=(('bold' if char == '█' else 'none') if theme is not None and theme.no_color else ('bold ' if char == '█' else '') + (theme.accent if theme is not None else '#82b4fa')))
    return art
