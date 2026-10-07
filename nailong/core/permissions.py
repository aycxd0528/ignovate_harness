"""Explainable, project-scoped permission decisions for Agent tools."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

import local_tools
from nailong.tools.registry import get_tool_specs


class Decision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class PermissionRule:
    tool: str
    specifier: str | None
    effect: Literal["allow", "deny", "ask"]
    source: Literal["project", "session"]
    text: str


@dataclass(frozen=True)
class PermissionResult:
    decision: Decision
    reason: str
    matched_rule: str | None = None
    suggested_rule: str | None = None


@dataclass(frozen=True)
class ApprovalDecision:
    kind: Literal["approve_once", "approve_session", "reject"]
    rule: str | None = None
    comment: str | None = None


_TOOL_RULE_NAMES = {
    "list_files": "Read",
    "read_file": "Read",
    "glob": "Read",
    "grep": "Read",
    "search_text": "Read",
    "load_skill": "Read",
    "read_skill_resource": "Read",
    "edit_file": "Edit",
    "write_file": "Write",
    "run_command": "Bash",
}
_TOOL_SPECS = {spec.name: spec for spec in get_tool_specs("chat")}
_RULE_RE = re.compile(r"^([A-Za-z_*][A-Za-z0-9_*.-]*)\((.*)\)$")
_PROTECTED_COMPONENTS = {".env", ".git", ".venv", "__pycache__"}


class PermissionEngine:
    """Resolve hard boundaries, modes, persisted rules, then safe defaults."""

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        settings_path: str | Path | None = None,
        rules: dict[str, list[str]] | None = None,
    ):
        self.project_root = Path(project_root or local_tools.PROJECT_ROOT).resolve()
        self.settings_path = Path(settings_path) if settings_path else self.project_root / ".nailong" / "settings.json"
        self._rules: list[PermissionRule] = []
        self._session_rules: list[PermissionRule] = []
        loaded = rules if rules is not None else self._load_rules()
        for effect in ("deny", "allow", "ask"):
            for text in loaded.get(effect, []):
                try:
                    self.add_rule(effect, text)
                except ValueError:
                    # A malformed user rule cannot widen permissions.
                    continue

    def _load_rules(self) -> dict[str, list[str]]:
        try:
            resolved_settings = self.settings_path.resolve(strict=False)
            if not resolved_settings.is_relative_to(self.project_root):
                return {"allow": [], "deny": [], "ask": []}
        except (OSError, RuntimeError):
            return {"allow": [], "deny": [], "ask": []}
        try:
            payload = json.loads(resolved_settings.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"allow": [], "deny": [], "ask": []}
        permissions = payload.get("permissions", {}) if isinstance(payload, dict) else {}
        if not isinstance(permissions, dict):
            return {"allow": [], "deny": [], "ask": []}
        return {
            effect: [item for item in permissions.get(effect, []) if isinstance(item, str)]
            if isinstance(permissions.get(effect, []), list)
            else []
            for effect in ("allow", "deny", "ask")
        }

    @staticmethod
    def parse_rule(
        effect: Literal["allow", "deny", "ask"],
        text: str,
        source: Literal["project", "session"] = "project",
    ) -> PermissionRule:
        if effect not in {"allow", "deny", "ask"}:
            raise ValueError("规则效果必须是 allow、deny 或 ask。")
        match = _RULE_RE.fullmatch(text.strip())
        if not match:
            raise ValueError(f"权限规则格式无效：{text}")
        tool, specifier = match.groups()
        tool = "*" if tool == "*" else tool
        return PermissionRule(
            tool=tool,
            specifier=specifier or None,
            effect=effect,
            source=source,
            text=text.strip(),
        )

    def add_rule(self, effect: Literal["allow", "deny", "ask"], text: str) -> None:
        self._rules.append(self.parse_rule(effect, text))

    def grant_session(self, text: str) -> PermissionRule:
        rule = self.parse_rule("allow", text, source="session")
        self._session_rules.append(rule)
        return rule

    def suggest_rule(self, name: str, args: dict) -> str:
        rule_name = _TOOL_RULE_NAMES.get(name, name)
        if name.startswith('mcp__'):
            return f'{name}(*)'
        if name == "run_command":
            command = str(args.get("command", "")).strip()
            if not command or self._split_shell_command(command) is None:
                return f"{rule_name}(<无法安全生成规则>)"
            return f"{rule_name}({command})"
        path = args.get("path")
        if path is None:
            return f"{rule_name}(*)"
        try:
            display_path = self._display_path(str(path), allow_missing=True)
        except ValueError:
            return f"{rule_name}(<路径无效>)"
        if any(character in display_path for character in "*?[]"):
            return f"{rule_name}(<路径包含通配符>)"
        return f"{rule_name}({display_path})"

    def decide_action(
        self,
        name: str,
        args: dict,
        *,
        profile: str = "chat",
        mode: str = "default",
        tool_spec=None,
    ) -> PermissionResult:
        rule_name = _TOOL_RULE_NAMES.get(name, name)
        spec = tool_spec if tool_spec is not None else _TOOL_SPECS.get(name)
        if spec is not None and spec.name != name:
            return PermissionResult(Decision.DENY, '工具元数据与当前执行模式不一致。')
        # Model responses are redacted before tool execution. Reject that marker
        # at this gate too, so skipping approval cannot execute secret arguments.
        if '[密钥已隐藏]' in json.dumps(args, ensure_ascii=False, default=str):
            return PermissionResult(Decision.DENY, '操作参数包含密钥脱敏标记，已拒绝。')
        read_only = bool(spec and spec.read_only)
        suggested = None if mode == 'bypassPermissions' else self.suggest_rule(name, args)

        # Normal modes retain the project boundary. Explicit full access skips
        # this path policy; the operating system still checks account privileges.
        path = args.get("path")
        normalized_path = None
        if (mode != 'bypassPermissions' and path is not None and name != "run_command"
                and not (spec is not None and spec.permission_key.startswith('mcp__'))):
            try:
                normalized_path = self._display_path(str(path), allow_missing=True)
            except ValueError as error:
                return PermissionResult(Decision.DENY, str(error), suggested_rule=suggested)

        if (mode == "plan" or profile == "plan") and not read_only and name != "exit_plan_mode":
            return PermissionResult(
                Decision.DENY,
                "计划模式禁止所有有副作用的工具。",
                suggested_rule=suggested,
            )
        if profile == "review" and not read_only:
            return PermissionResult(
                Decision.DENY,
                "审查模式是只读模式。",
                suggested_rule=suggested,
            )
        if profile == "subagent" and not read_only:
            return PermissionResult(Decision.DENY, '只读子代理不能执行有副作用的工具。',
                suggested_rule=suggested)
        if profile == "init" and not read_only:
            if name != "write_file" or not local_tools.is_exact_project_path(
                str(path or ""), ".nailong/context.md"
            ):
                return PermissionResult(
                    Decision.DENY,
                    "初始化模式只能写入 .nailong/context.md。",
                    suggested_rule=suggested,
                )

        if spec is not None and profile not in spec.profiles:
            return PermissionResult(Decision.DENY, '工具元数据与当前执行模式不一致。')

        if mode == 'bypassPermissions' and spec is not None:
            return PermissionResult(Decision.ALLOW,
                '本次启动跳过权限规则与逐项审批，允许任意文件路径；仍受系统账户权限和运行模式限制。',
                suggested_rule=suggested)

        candidates = self._rules + self._session_rules
        matching = [
            rule
            for rule in candidates
            if self._rule_matches(rule, rule_name, name, args, normalized_path)
        ]
        denied = next((rule for rule in matching if rule.effect == "deny"), None)
        if denied:
            return PermissionResult(
                Decision.DENY,
                f"命中拒绝规则：{denied.text}",
                denied.text,
                suggested,
            )
        allowed = next((rule for rule in matching if rule.effect == "allow"), None)
        allowed_text = allowed.text if allowed else None
        if rule_name == "Bash":
            segments = self._split_shell_command(str(args.get("command", "")))
            allow_rules = [rule for rule in candidates if rule.effect == "allow" and rule.tool.casefold() in {"*", rule_name.casefold()}]
            segment_rules = [
                [
                    rule
                    for rule in allow_rules
                    if rule.specifier is not None
                    and self._command_matches(rule.specifier, segment)
                ]
                for segment in segments or []
            ]
            if segments and all(segment_rules):
                used_rules = list(dict.fromkeys(rule for matched in segment_rules for rule in matched))
                allowed = used_rules[0]
                allowed_text = ", ".join(rule.text for rule in used_rules)
            else:
                allowed = None
                allowed_text = None
        if allowed:
            return PermissionResult(
                Decision.ALLOW,
                f"命中允许规则：{allowed_text or allowed.text}",
                allowed_text or allowed.text,
                suggested,
            )
        if mode == "plan" and name == "exit_plan_mode":
            return PermissionResult(
                Decision.ALLOW,
                "计划模式唯一允许的非只读工具：提交计划供用户审批。",
                suggested_rule=suggested,
            )
        if name == "update_goal":
            return PermissionResult(
                Decision.ALLOW,
                "目标状态更新受 GoalStore 护栏约束，不直接操作项目文件或命令。",
                suggested_rule=suggested,
            )
        ask = next((rule for rule in matching if rule.effect == "ask"), None)
        if ask:
            return PermissionResult(
                Decision.ASK,
                f"命中确认规则：{ask.text}",
                ask.text,
                suggested,
            )
        if mode in {"acceptEdits", "accept_edits"} and name in {"edit_file", "write_file"}:
            return PermissionResult(
                Decision.ALLOW,
                "当前模式允许文件编辑；命令仍需逐项审批。",
                suggested_rule=suggested,
            )
        if read_only:
            return PermissionResult(Decision.ALLOW, "只读工具默认允许。", suggested_rule=suggested)
        return PermissionResult(
            Decision.ASK,
            "外部 MCP 工具默认需要你审批。" if spec is not None and spec.permission_key.startswith('mcp__') else "该操作会修改文件或运行命令，默认需要你审批。",
            suggested_rule=suggested,
        )

    def explain(self, name: str, args: dict, *, profile: str = "chat", mode: str = "default") -> str:
        return self.decide_action(name, args, profile=profile, mode=mode).reason

    def format_rules(self) -> str:
        lines = [f"项目权限配置：{self.settings_path}"]
        if not self._rules and not self._session_rules:
            lines.append("当前没有自定义规则；只读工具默认允许，文件修改和命令运行默认询问。")
        for effect in ("deny", "allow", "ask"):
            for rule in self._rules:
                if rule.effect == effect:
                    lines.append(f"{effect}: {rule.text}")
        for rule in self._session_rules:
            lines.append(f"本会话允许: {rule.text}")
        lines.append("保护路径与项目根目录边界优先于所有规则。命令审批不构成操作系统沙箱。")
        return "\n".join(lines)

    def _display_path(self, path: str, *, allow_missing: bool) -> str:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        # Absolute aliases (e.g. macOS /var -> /private/var) can refer to
        # this project. Preserve lexical protection before resolving aliases;
        # the resolved path below remains the authority for the root boundary.
        lexical_relative = (candidate.relative_to(self.project_root)
            if candidate.is_relative_to(self.project_root) else candidate)
        if self._has_protected_component(lexical_relative):
            raise ValueError("受保护路径不能通过文件工具访问。")
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as error:
            raise ValueError("路径无法解析，已拒绝。") from error
        if not resolved.is_relative_to(self.project_root):
            raise ValueError("路径超出项目根目录，已拒绝。")
        relative = resolved.relative_to(self.project_root)
        if self._has_protected_component(relative):
            raise ValueError("受保护路径不能通过文件工具访问。")
        if not allow_missing and not resolved.exists():
            raise ValueError("目标路径不存在。")
        return "./" + relative.as_posix() if relative.parts else "./"

    @staticmethod
    def _has_protected_component(relative: Path) -> bool:
        for part in relative.parts:
            folded = part.casefold()
            if folded in _PROTECTED_COMPONENTS or folded == ".env" or folded.startswith(".env."):
                return True
        return False

    def _rule_matches(
        self,
        rule: PermissionRule,
        rule_name: str,
        tool_name: str,
        args: dict,
        normalized_path: str | None,
    ) -> bool:
        if tool_name.startswith('mcp__'):
            if rule.tool not in {'*', tool_name}:
                return False
        elif rule.tool.casefold() not in {"*", rule_name.casefold()}:
            return False
        if rule.specifier is None or rule.specifier == "*":
            return True
        if rule_name == "Bash":
            command = str(args.get("command", "")).strip()
            segments = self._split_shell_command(command)
            if segments is None:
                return rule.effect in {"deny", "ask"} and rule.specifier == "*"
            return any(self._command_matches(rule.specifier, segment) for segment in segments)
        if normalized_path is None:
            return False
        pattern = rule.specifier.removeprefix("./")
        path = normalized_path.removeprefix("./")
        if fnmatch.fnmatchcase(path, pattern):
            return True
        # **/ can consume zero leading directories; exact rules stay anchored.
        while pattern.startswith('**/'):
            pattern = pattern[3:]
            if fnmatch.fnmatchcase(path, pattern):
                return True
        return False

    @staticmethod
    def _command_matches(pattern: str, command: str) -> bool:
        candidate = " ".join(command.split())
        allowed = " ".join(pattern.split())
        if allowed.endswith(":*"):
            prefix = allowed[:-2].rstrip()
            return candidate == prefix or (
                candidate.startswith(prefix)
                and len(candidate) > len(prefix)
                and candidate[len(prefix)] in {" ", "/", ":"}
            )
        return fnmatch.fnmatchcase(candidate, allowed)

    @staticmethod
    def _split_shell_command(command: str) -> list[str] | None:
        """Split only simple shell lists; return None for constructs needing review."""
        if not command.strip() or any(marker in command for marker in ("$", "`", "<", ">")):
            return None
        segments: list[str] = []
        current: list[str] = []
        quote: str | None = None
        index = 0
        while index < len(command):
            char = command[index]
            if char == "\\":
                # Backslash escapes change shell token boundaries; ask instead.
                return None
            if char in {"'", '"'}:
                if quote is None:
                    quote = char
                elif quote == char:
                    quote = None
                current.append(char)
                index += 1
                continue
            if quote is None and char in {"(", ")"}:
                return None
            if quote is None and char == "&":
                if index + 1 >= len(command) or command[index + 1] != "&":
                    return None
                separator = "&&"
                index += 1
                segment = "".join(current).strip()
                if not segment:
                    return None
                segments.append(segment)
                current.clear()
                index += 1
                continue
            if quote is None and char in {";", "|", "\n"}:
                separator = char
                if char in {";", "|"} and index + 1 < len(command) and command[index + 1] == char:
                    if char == "|":
                        separator = "||"
                        index += 1
                segment = "".join(current).strip()
                if not segment:
                    return None
                segments.append(segment)
                current.clear()
                if separator == "\n":
                    while index + 1 < len(command) and command[index + 1] == "\n":
                        index += 1
                index += 1
                continue
            current.append(char)
            index += 1
        if quote is not None:
            return None
        final = "".join(current).strip()
        if not final:
            return None
        segments.append(final)
        try:
            for segment in segments:
                words = shlex.split(segment, posix=True)
                if not words or words[0] == "cd":
                    return None
        except ValueError:
            return None
        return segments
