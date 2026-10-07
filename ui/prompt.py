"""prompt_toolkit input, history, slash commands, and project-scoped @ paths."""

from __future__ import annotations

import os
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings

import local_tools


class RedactingFileHistory(FileHistory):
    """Persist prompt history without ever writing the configured model key."""

    def __init__(self, filename: str, api_key: str = ""):
        super().__init__(filename)
        self.api_key = api_key

    def store_string(self, string: str) -> None:
        if self.api_key:
            string = string.replace(self.api_key, "[密钥已隐藏]")
        super().store_string(string)
        try:
            os.chmod(self.filename, 0o600)
        except OSError:
            pass

    def load_history_strings(self):
        strings = list(super().load_history_strings())
        if not self.api_key:
            return iter(strings)
        sanitized = [item.replace(self.api_key, "[密钥已隐藏]") for item in strings]
        if sanitized != strings:
            try:
                with open(self.filename, "wb"):
                    pass
                for item in reversed(sanitized):
                    self.store_string(item)
            except OSError:
                pass
        return iter(sanitized)


class SlashCompleter(Completer):
    def __init__(self, commands: dict[str, str]):
        self.commands = commands

    def get_completions(self, document, complete_event):
        before = document.text_before_cursor
        if not before.startswith(("/","$")) or " " in before:
            return
        for name, description in self.commands.items():
            if name.startswith(before):
                yield Completion(name, start_position=-len(before), display_meta=description)


class AtFileCompleter(Completer):
    """Complete filesystem entries under project_root only.

    The @ fragment may start anywhere in the input (e.g. "请查看 @src/ma"),
    not just at the beginning of the line.
    """

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()

    def _fragment(self, before: str) -> str | None:
        at = before.rfind("@")
        while at > 0 and not before[at - 1].isspace():
            at = before.rfind("@", 0, at)
        if at < 0:
            return None
        fragment = before[at + 1:]
        if any(char.isspace() for char in fragment):
            return None
        return fragment

    def get_completions(self, document, complete_event):
        before = document.text_before_cursor
        fragment = self._fragment(before)
        if fragment is None:
            return
        relative = Path(fragment)
        if relative.is_absolute():
            return
        parent_part = relative.parent if fragment and not fragment.endswith("/") else relative
        search = "" if not fragment or fragment.endswith("/") else relative.name
        parent = (self.project_root / parent_part).resolve()
        try:
            parent.relative_to(self.project_root)
        except ValueError:
            return
        if not parent.is_dir():
            return
        entries = sorted(parent.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold()))
        for entry in entries:
            if search and not entry.name.startswith(search):
                continue
            try:
                resolved = entry.resolve()
                resolved.relative_to(self.project_root)
            except (OSError, ValueError):
                continue
            relative_path = resolved.relative_to(self.project_root)
            if any(local_tools._is_protected_component(part) for part in relative_path.parts):
                continue
            completion = str(relative_path)
            if entry.is_dir():
                completion += "/"
            yield Completion(
                completion,
                start_position=-len(fragment),
                display_meta="目录" if entry.is_dir() else "文件",
            )


class PrefixCompleter(Completer):
    def __init__(self, project_root: str | Path, commands: dict[str, str]):
        self.slash = SlashCompleter(commands)
        self.paths = AtFileCompleter(project_root)

    def get_completions(self, document, complete_event):
        before = document.text_before_cursor
        if before.startswith(("/","$")) and " " not in before:
            yield from self.slash.get_completions(document, complete_event)
        else:
            yield from self.paths.get_completions(document, complete_event)


def build_session(
    project_root: str | Path,
    commands: dict[str, str],
    *,
    history_path: str | Path | None = None,
    api_key: str = "",
    input=None,
    output=None,
) -> PromptSession:
    history = Path(history_path).expanduser() if history_path else Path.home() / ".nailong" / "history"
    history.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(history.parent, 0o700)
        if history.exists():
            os.chmod(history, 0o600)
    except OSError:
        pass
    bindings = KeyBindings()

    @bindings.add("c-l")
    def _clear_screen(event):
        renderer = getattr(event.app, "renderer", None)
        if renderer is not None:
            renderer.clear()
        event.app.invalidate()

    return PromptSession(
        input=input,
        output=output,
        history=RedactingFileHistory(str(history), api_key=api_key),
        completer=PrefixCompleter(project_root, commands),
        complete_while_typing=True,
        multiline=False,
        key_bindings=bindings,
    )
