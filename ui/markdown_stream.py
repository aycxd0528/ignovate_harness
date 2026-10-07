"""Block-level buffering that turns a token stream into whole Markdown blocks.

Streaming renderers cannot re-render Markdown per token: blocks are only
meaningful once complete. This buffer releases text at paragraph boundaries
(blank lines) and closed fenced code blocks, so the terminal can render
styled Markdown (headings, lists, syntax-highlighted code) while the reply
is still arriving.

CRLF input is normalized to LF so boundary detection works uniformly.
"""

from __future__ import annotations

_DEFAULT_MAX_BLOCK_CHARS = 8_000


def _opening_fence_at(text: str, start: int = 0) -> int | None:
    """Index of a ``` line start at or after `start`; None when absent."""
    position = start
    while True:
        index = text.find("```", position)
        if index < 0:
            return None
        line_start = text.rfind("\n", 0, index) + 1
        if text[line_start:index].strip() == "":
            return index
        position = index + 3


def _closing_fence_end(text: str, opening: int) -> int | None:
    """Index just past the closing ``` line, or None when the fence is open."""
    rest = text[opening + 3:]
    position = 0
    while True:
        newline = rest.find("\n", position)
        if newline < 0:
            return None
        line_start = newline + 1
        next_newline = rest.find("\n", line_start)
        line = rest[line_start:] if next_newline < 0 else rest[line_start:next_newline]
        stripped = line.strip()
        if stripped.startswith("```") and stripped[3:].strip() == "":
            end = len(text) if next_newline < 0 else opening + 3 + next_newline
            return end
        if next_newline < 0:
            return None
        position = line_start


class MarkdownStreamBuffer:
    """Accumulate streamed text and release complete Markdown blocks.

    `feed()` returns blocks that are safe to render; `flush()` returns the
    remainder. A paragraph directly followed by a fenced code block is split
    at the fence so the fence never renders inside a paragraph.
    """

    def __init__(self, max_block_chars: int = _DEFAULT_MAX_BLOCK_CHARS):
        self.max_block_chars = max(1, int(max_block_chars))
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        """Append a token chunk and return every newly complete block."""
        if not text:
            return []
        self._buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        return self._drain(final=False)

    def flush(self) -> str:
        """Return whatever remains buffered, emptying the buffer."""
        remainder = self._buffer
        self._buffer = ""
        return remainder

    def preview(self, max_chars: int = 80) -> str:
        """Return the latest nonempty line for the transient progress display."""
        for line in reversed(self._buffer.splitlines()):
            if line.strip():
                return line.strip()[-max(1, max_chars):]
        return ""

    # -- internals ---------------------------------------------------------

    def _drain(self, *, final: bool) -> list[str]:
        blocks: list[str] = []
        while True:
            block, rest = self._take_block(self._buffer, final=final)
            if block is None:
                self._buffer = rest
                return blocks
            self._buffer = rest
            if block:
                blocks.append(block)
            if not rest:
                return blocks

    def _take_block(self, text: str, *, final: bool) -> tuple[str | None, str]:
        text = text.lstrip("\n")
        if not text:
            return None, ""
        fence = _opening_fence_at(text)
        if fence == 0:
            closing = _closing_fence_end(text, 0)
            if closing is not None:
                return text[:closing], text[closing:]
            if final or len(text) >= self.max_block_chars:
                # Never grow without bound: force the open fence out.
                return text.rstrip(), ""
            return None, text
        if fence is not None:
            # A paragraph directly precedes a fence: release it first.
            return text[:fence].rstrip(), text[fence:]
        boundary = text.find("\n\n")
        if boundary >= 0:
            return text[:boundary].rstrip(), text[boundary + 1:].lstrip("\n")
        if len(text) >= self.max_block_chars:
            cut = self.max_block_chars
            last_newline = text.rfind("\n", 0, cut)
            if last_newline > cut // 2:
                return text[:last_newline].rstrip(), text[last_newline + 1:]
            return text[:cut].rstrip(), text[cut:]
        if final:
            return text.rstrip(), ""
        return None, text
