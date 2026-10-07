"""Small terminal theme with Unicode and color fallbacks."""

from __future__ import annotations

import locale
import os
import sys
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Theme:
    name: str = "dark"
    background: str = "#11161c"
    foreground: str = "#dde3e8"
    surface: str = "#1b242e"
    border: str = "#526577"
    accent: str = "#88c0d0"
    success: str = "#3fb950"
    danger: str = "#e58a8a"
    muted: str = "#8c9aa8"
    glyph_running: str = "●"
    glyph_ok: str = "✓"
    glyph_fail: str = "✗"
    no_color: bool = False
    code_theme: str = "monokai"
    # Transcript palette: semantic tokens instead of scattered hex codes.
    role_user: str | None = "#ddb775"
    role_assistant: str | None = "#88c0d0"
    role_error: str | None = "#ff6b6b"
    body_user: str | None = "#e3e8ec"
    tool_label: str | None = "#8c9aa8"
    tool_line: str | None = "#a1adb9"
    gutter: str = "▌"
    bullet: str = "·"

    def role_color(self, role: str) -> str | None:
        """Marker color for a transcript role, or None when colors are off."""
        return {
            "user": self.role_user,
            "assistant": self.role_assistant,
            "error": self.role_error,
        }.get(role, self.muted)


_PALETTE_KEYS = ("role_user", "role_assistant", "role_error", "body_user", "tool_label", "tool_line")


def load_theme(*, ascii_only: bool = False, encoding: str | None = None, name: str = "dark") -> Theme:
    """Honor NO_COLOR and fall back to ASCII on limited terminal encodings."""
    encoding = encoding or getattr(sys.stdout, "encoding", None) or locale.getpreferredencoding(False)
    ascii_terminal = (encoding or "").lower().replace("-", "") in {"ascii", "usascii"}
    use_ascii = ascii_only or ascii_terminal or os.environ.get("LC_ALL") == "C"
    no_color = "NO_COLOR" in os.environ
    palette = {key: None for key in _PALETTE_KEYS} if no_color else {}
    if use_ascii:
        theme = Theme(
            glyph_running=">",
            glyph_ok="+",
            glyph_fail="!",
            no_color=no_color,
            gutter="|",
            bullet="-",
            **palette,
        )
    else:
        theme = Theme(no_color=no_color, **palette)
    if name not in {"dark","light","ansi"}: raise ValueError("主题只能为 dark、light 或 ansi。")
    changes = {"name":name}
    if name == "light":
        changes.update(background="#f5f6f7",foreground="#28343c",surface="#e9edf1",border="#8c9aa8",accent="#235a74",success="#137238",danger="#a52929",code_theme="friendly",muted="#596671")
        if not no_color:
            changes.update(role_user="#80531f",role_assistant="#235a74",role_error="#a52929",
                           body_user="#28343c",tool_label="#5a6770",tool_line="#596671",muted="#596671")
    elif name == "ansi" and not no_color:
        changes.update(role_user="yellow",role_assistant="cyan",role_error="red",body_user="default",
                       tool_label="bright_black",tool_line="bright_black",muted="bright_black")
    return replace(theme,**changes)


def semantic_rich_theme(theme):
    from rich.theme import Theme as RichTheme
    from rich.style import Style
    accent=Style(color=None if theme.no_color else theme.accent)
    body=Style(color=None if theme.no_color else theme.foreground)
    heading=Style(color=None if theme.no_color else theme.accent,bold=True)
    return RichTheme({**{f'markdown.h{level}':heading for level in range(1,7)},
        'markdown.h1.border':accent,'markdown.code':heading,
        'markdown.link':accent,'markdown.link_url':accent,'markdown.paragraph':body,
        'markdown.item':body,'markdown.item.bullet':accent,'markdown.table.header':heading})
