"""Markdown slash-command discovery with permission-narrowing metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
import local_tools


@dataclass(frozen=True)
class PromptCommand:
    name: str
    description: str
    prompt: str
    allowed_tools: frozenset[str] | None
    model_profile: str


class CommandRegistry:
    def __init__(
        self,
        project_root: str | Path,
        *,
        user_root: str | Path | None = None,
        builtins_root: str | Path | None = None,
        api_key: str = "",
    ):
        root = Path(project_root).resolve()
        self.project_root = root
        self.api_key = api_key
        self.directories = (
            (Path(builtins_root) if builtins_root is not None else Path(__file__).parents[1] / "commands", False),
            (root / ".nailong" / "commands", True),
            (Path(user_root).expanduser() if user_root is not None else Path.home() / ".nailong" / "commands", False),
        )

    def _safe_project_path(self, path: Path) -> bool:
        if not path.is_relative_to(self.project_root):
            return False
        parts = (part.casefold() for part in path.relative_to(self.project_root).parts)
        return not any(part in {'.env', '.git', '.venv', '__pycache__'}
                       or part.startswith('.env.') for part in parts)

    @staticmethod
    def _parse(path: Path) -> tuple[dict, str]:
        with local_tools.open_regular_file(path, binary=False) as source:
            text = source.read()
        if not text.startswith("---\n"):
            return {}, text.strip()
        end = text.find("\n---", 4)
        if end < 0:
            return {}, text.strip()
        metadata = yaml.safe_load(text[4:end]) or {}
        if not isinstance(metadata, dict):
            metadata = {}
        return metadata, text[end + 4:].strip()

    def list_commands(self) -> dict[str, tuple[str, Path]]:
        result: dict[str, tuple[str, Path]] = {}
        for directory, project_scoped in self.directories:
            try:
                resolved_directory = directory.resolve(strict=True)
                if project_scoped and not self._safe_project_path(resolved_directory):
                    continue
                paths = sorted(resolved_directory.glob("*.md"), key=lambda item: item.name)
            except OSError:
                continue
            for path in paths:
                try:
                    resolved_path = path.resolve(strict=True)
                    if project_scoped and not self._safe_project_path(resolved_path):
                        continue
                except (OSError, RuntimeError):
                    continue
                if resolved_path.is_file():
                    try:
                        metadata, _ = self._parse(resolved_path)
                    except (OSError, UnicodeError, yaml.YAMLError):
                        continue
                    description = str(metadata.get("description", "自定义命令"))
                    if self.api_key:
                        description = description.replace(self.api_key, "[密钥已隐藏]")
                    result[path.stem] = (description, resolved_path)
        return result

    def resolve(
        self,
        name: str,
        arguments: list[str],
        *,
        available_tools: set[str] | frozenset[str],
    ) -> PromptCommand | None:
        path = self.list_commands().get(name, ("", None))[1]
        if path is None:
            return None
        metadata, body = self._parse(path)
        available = set(available_tools)
        declared = metadata.get("allowed-tools")
        if isinstance(declared, str):
            declared = [declared]
        allowed = frozenset(available.intersection(item for item in declared if isinstance(item, str))) if isinstance(declared, list) else None
        expanded = body.replace("$ARGUMENTS", " ".join(arguments))
        for index, argument in enumerate(arguments[:9], start=1):
            expanded = expanded.replace(f"${index}", argument)
        profile = str(metadata.get("model-profile", "chat"))
        if profile not in {"chat", "init", "review", "plan"}:
            profile = "chat"
        description = str(metadata.get("description", "自定义命令"))
        if self.api_key:
            expanded = expanded.replace(self.api_key, "[密钥已隐藏]")
            description = description.replace(self.api_key, "[密钥已隐藏]")
        return PromptCommand(
            name=name,
            description=description,
            prompt=expanded,
            allowed_tools=allowed,
            model_profile=profile,
        )
