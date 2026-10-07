"""Static dashboard banner with terminal-size and color fallbacks."""

from __future__ import annotations

import json
from pathlib import Path

from rich.text import Text

from ui.theme import Theme, load_theme


_LETTERS = (
    ("█████", "  █  ", "  █  ", "  █  ", "  █  ", "█████"),  # I
    (" ████", "█    ", "█  ██", "█   █", "█   █", " ████"),  # G
    ("█   █", "██  █", "█ █ █", "█  ██", "█   █", "█   █"),  # N
    (" ███ ", "█   █", "█   █", "█   █", "█   █", " ███ "),  # O
    ("█   █", "█   █", "█   █", "█   █", " █ █ ", "  █  "),  # V
    (" ███ ", "█   █", "█████", "█   █", "█   █", "█   █"),  # A
    ("█████", "  █  ", "  █  ", "  █  ", "  █  ", "  █  "),  # T
    ("█████", "█    ", "████ ", "█    ", "█    ", "█████"),  # E
)
BANNER_ROWS: tuple[str, ...] = tuple("  ".join(letter[row] for letter in _LETTERS) for row in range(6))

# Top-to-bottom gradient so the banner reads as depth instead of a flat block.
_BANNER_GRADIENT = ("#7df6fb", "#5ff3f9", "#46e9f1", "#37d6e3", "#2cc0d2", "#25a8bd")


def configured_banner_enabled(project_root: str | Path) -> bool:
    """Read the optional project setting without following it outside the root."""
    root = Path(project_root).resolve()
    try:
        path = (root / ".nailong" / "settings.json").resolve(strict=True)
        if not path.is_relative_to(root):
            return True
        payload = json.loads(path.read_text(encoding="utf-8"))
        ui = payload.get("ui", {}) if isinstance(payload, dict) else {}
        value = ui.get("banner") if isinstance(ui, dict) else None
        return value if isinstance(value, bool) else True
    except (OSError, UnicodeError, ValueError, RuntimeError):
        return True


def banner_renderable(
    *, width: int, height: int, theme: Theme | None = None, enabled: bool = True
) -> Text | None:
    if not enabled or width < 72 or height < 24:
        return None
    theme = theme or load_theme()
    ascii_terminal = theme.glyph_running == ">"
    if width < 80 or height < 30 or ascii_terminal:
        return Text("ignovate harness", style=None if theme.no_color else theme.accent)
    art = Text()
    for index, row in enumerate(BANNER_ROWS):
        if index:
            art.append("\n")
        art.append(row, style=_BANNER_GRADIENT[min(index, len(_BANNER_GRADIENT) - 1)])
    return art


def configured_reduced_motion(project_root):
    """Optional terminal-friendly motion setting, restricted to project config."""
    root = Path(project_root).resolve()
    try:
        path = (root / '.nailong' / 'settings.json').resolve(strict=True)
        if not path.is_relative_to(root):
            return False
        payload = json.loads(path.read_text(encoding='utf-8'))
        return payload.get('ui', {}).get('reduced_motion') is True
    except (OSError, UnicodeError, ValueError, RuntimeError, AttributeError):
        return False
