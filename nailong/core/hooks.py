"""Explicitly approved project lifecycle hooks with a filtered subprocess env."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import local_tools


@dataclass(frozen=True)
class HookOutput:
    event: str
    command: str
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool = False


@dataclass(frozen=True)
class HookRunResult:
    outputs: tuple[HookOutput, ...] = ()
    blocked: bool = False
    reason: str = ""

    @property
    def feedback(self) -> str:
        return "\n".join(
            output for item in self.outputs for output in (item.stdout.strip(), item.stderr.strip()) if output
        )


class HookRunner:
    def __init__(
        self,
        project_root: str | Path,
        *,
        api_key: str = "",
        timeout_seconds: float = 10,
        max_output_chars: int = 12_000,
    ):
        self.project_root = Path(project_root).resolve()
        self.api_key = api_key
        self.timeout_seconds = max(0.1, min(float(timeout_seconds), 10.0))
        self.max_output_chars = max(256, min(int(max_output_chars), 12_000))
        self.settings_path = self.project_root / ".nailong" / "settings.json"
        self.local_settings_path = self.project_root / ".nailong" / "settings.local.json"
        self._settings_lock = threading.RLock()

    def _safe_read(self, path: Path) -> dict:
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.project_root):
                return {}
            payload = json.loads(resolved.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError):
            return {}

    def _approved_commands(self) -> set[str]:
        values = self._safe_read(self.local_settings_path).get("approved_hook_hashes", [])
        return {item for item in values if isinstance(item, str)} if isinstance(values, list) else set()

    def _save_approval(self, fingerprint: str) -> None:
        with self._settings_lock:
            parent = self.local_settings_path.parent
            parent.mkdir(parents=True, exist_ok=True)
            resolved_parent = parent.resolve(strict=True)
            if not resolved_parent.is_relative_to(self.project_root):
                raise ValueError("钩子本地配置必须位于项目根目录内。")
            current = self._safe_read(self.local_settings_path)
            approved = self._approved_commands()
            approved.add(fingerprint)
            current["approved_hook_hashes"] = sorted(approved)
            descriptor, temporary_name = tempfile.mkstemp(prefix=".settings.local.", dir=resolved_parent)
            temporary = Path(temporary_name)
            try:
                os.fchmod(descriptor, 0o600)
                payload = memoryview(json.dumps(current, ensure_ascii=False, indent=2).encode("utf-8"))
                while payload:
                    written = os.write(descriptor, payload)
                    payload = payload[written:]
                os.close(descriptor)
                descriptor = -1
                os.replace(temporary, self.local_settings_path)
                try:
                    os.chmod(self.local_settings_path, 0o600)
                except OSError:
                    pass
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _fingerprint(command: str) -> str:
        return hashlib.sha256(command.encode("utf-8")).hexdigest()

    async def run_event(
        self,
        event: str,
        *,
        tool_name: str = "",
        path: str = "",
        prompt: str = "",
        confirm=None,
    ) -> HookRunResult:
        definitions = self._safe_read(self.settings_path).get("hooks", {})
        entries = definitions.get(event, []) if isinstance(definitions, dict) else []
        if not isinstance(entries, list):
            return HookRunResult()
        outputs = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            matcher = str(entry.get("matcher", ".*"))
            try:
                if not re.search(matcher, tool_name):
                    continue
            except re.error:
                continue
            hooks = entry.get("hooks", [])
            if not isinstance(hooks, list):
                continue
            for hook in hooks:
                if not isinstance(hook, dict) or hook.get("type") != "command":
                    continue
                command = str(hook.get("command", "")).strip()
                if not command:
                    continue
                if self.api_key and self.api_key in command:
                    return HookRunResult(tuple(outputs), event in {"PreToolUse", "UserPromptSubmit"}, "钩子命令包含模型密钥，已拒绝。")
                fingerprint = self._fingerprint(command)
                if fingerprint not in self._approved_commands():
                    approved = False
                    if confirm is not None:
                        action = {
                            "name": "hook",
                            "args": {"event": event, "command": command},
                            "_approval": {"reason": "首次执行此项目钩子需要确认。"},
                        }
                        answer = confirm(action)
                        if inspect.isawaitable(answer):
                            answer = await answer
                        approved = answer is True or getattr(answer, "kind", None) in {"approve_once", "approve_session"} or str(answer).strip().lower() in {"a", "approve", "approve_once", "yes", "y", "true"}
                    if not approved:
                        block = event in {"PreToolUse", "UserPromptSubmit"}
                        return HookRunResult(tuple(outputs), block, "钩子命令未获批准，未执行。")
                    try:
                        self._save_approval(fingerprint)
                    except OSError:
                        return HookRunResult(tuple(outputs), event in {"PreToolUse", "UserPromptSubmit"}, "无法安全保存钩子批准记录。")

                environment = local_tools._command_environment()
                environment.update({
                    "NAILONG_TOOL_NAME": tool_name,
                    "NAILONG_FILE_PATH": path,
                    "NAILONG_PROMPT": prompt.replace(self.api_key, "[密钥已隐藏]") if self.api_key else prompt,
                })
                result = await asyncio.to_thread(self._execute, command, environment)
                outputs.append(HookOutput(
                    event=event,
                    command="[已批准的项目钩子]",
                    stdout=result.stdout,
                    stderr=result.stderr,
                    exit_code=result.exit_code,
                    timed_out=result.timed_out,
                ))
                if (
                    event in {"PreToolUse", "UserPromptSubmit"}
                    and result.exit_code == 2
                    and not result.timed_out
                ):
                    return HookRunResult(tuple(outputs), True, result.stderr or result.stdout or "钩子阻止了操作。")
        return HookRunResult(tuple(outputs))

    def _execute(self, command: str, environment: dict[str, str]) -> HookOutput:
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=self.project_root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=(os.name == "posix"),
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            stdout, stderr = process.communicate()
        if self.api_key:
            stdout = stdout.replace(self.api_key, "[密钥已隐藏]")
            stderr = stderr.replace(self.api_key, "[密钥已隐藏]")
        return HookOutput(
            event="",
            command="[已批准的项目钩子]",
            stdout=stdout[: self.max_output_chars],
            stderr=stderr[: self.max_output_chars],
            exit_code=process.returncode,
            timed_out=timed_out,
        )
