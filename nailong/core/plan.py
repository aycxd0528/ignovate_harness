"""Plan drafts staged in memory and persisted only after explicit approval."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from nailong.core.process_io import OwnedProcess, command_environment, join_cleanup, split_editor_command
from nailong.core.safe_files import atomic_write_bytes, private_file_permissions


@dataclass(frozen=True)
class ApprovedPlan:
    plan_id: str
    path: Path
    markdown: str


class PlanStore:
    def __init__(self, project_root: str | Path, *, api_key: str = ""):
        self.project_root = Path(project_root).resolve()
        self.api_key = api_key
        self._pending: dict[str, str] = {}

    def stage(self, markdown: str) -> str:
        if not isinstance(markdown, str) or not markdown.strip():
            raise ValueError("计划内容不能为空。")
        if len(markdown) > 80_000:
            raise ValueError("计划不能超过 80,000 个字符。")
        safe_markdown = markdown.replace(self.api_key, "[密钥已隐藏]") if self.api_key else markdown
        plan_id = uuid.uuid4().hex
        self._pending[plan_id] = safe_markdown.strip() + "\n"
        return plan_id

    def get(self, plan_id: str) -> str | None:
        return self._pending.get(plan_id)

    def discard(self, plan_id: str) -> None:
        self._pending.pop(plan_id, None)

    def approve(self, plan_id: str, markdown: str | None = None) -> ApprovedPlan:
        if plan_id not in self._pending:
            raise KeyError("找不到待审批的计划。")
        content = markdown if markdown is not None else self._pending[plan_id]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("计划内容不能为空。")
        if self.api_key:
            content = content.replace(self.api_key, "[密钥已隐藏]")
        content = content.strip() + "\n"
        if len(content) > 80_000:
            raise ValueError("计划不能超过 80,000 个字符。")

        plans_dir = self.project_root / ".nailong" / "plans"
        if os.name == 'nt':
            from nailong.core.preferences import safe_config_path
            safe_config_path(plans_dir, self.project_root)
        plans_dir.mkdir(parents=True, exist_ok=True)
        resolved_dir = plans_dir.resolve(strict=True)
        if not resolved_dir.is_relative_to(self.project_root):
            raise ValueError("计划目录必须位于项目根目录内。")
        relative_parts = resolved_dir.relative_to(self.project_root).parts
        if any(part == ".env" or part.startswith(".env.") or part in {".git", ".venv", "__pycache__"} for part in relative_parts):
            raise ValueError("计划目录不能指向密钥或项目内部目录。")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = resolved_dir / f"{stamp}-{plan_id[:12]}.md"
        if os.name == 'nt':
            atomic_write_bytes(path, content.encode('utf-8'), replace=False)
            self._pending.pop(plan_id, None)
            return ApprovedPlan(plan_id, path, content)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        try:
            remaining = memoryview(content.encode("utf-8"))
            while remaining:
                written = os.write(descriptor, remaining)
                remaining = remaining[written:]
        finally:
            os.close(descriptor)
        self._pending.pop(plan_id, None)
        return ApprovedPlan(plan_id, path, content)


def edit_plan_with_editor(markdown: str, *, editor: str | None = None) -> str:
    """Open a private temporary copy with the user's configured editor."""
    import subprocess
    import tempfile

    selected = editor or os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not selected:
        raise ValueError("尚未设置 VISUAL 或 EDITOR 环境变量，无法编辑计划。")
    command = split_editor_command(selected)
    if not command:
        raise ValueError("编辑器命令为空。")
    descriptor, filename = tempfile.mkstemp(prefix="nailong-plan-", suffix=".md")
    path = Path(filename)
    owner = None
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            if os.name == 'nt':
                private_file_permissions(path)
            output.write(markdown)
        if os.name == 'nt':
            owner = OwnedProcess([*command, str(path)], env=command_environment())
            returncode = owner.process.wait(timeout=3600)
        else:
            # A terminal editor must retain the caller's controlling terminal.
            returncode = subprocess.run([*command, str(path)], env=command_environment(), timeout=3600).returncode
        if returncode:
            raise subprocess.CalledProcessError(returncode, [*command, str(path)])
        return path.read_text(encoding="utf-8")
    finally:
        if owner is not None:
            owner.close()
        path.unlink(missing_ok=True)


async def edit_plan_with_editor_async(markdown: str) -> str:
    """Own the terminal editor process until exit or cancellation, on a private copy."""
    import asyncio
    import tempfile
    selected = os.environ.get('VISUAL') or os.environ.get('EDITOR')
    if not selected:
        raise ValueError('尚未设置 VISUAL 或 EDITOR，无法启动编辑器。')
    command = split_editor_command(selected)
    if not command:
        raise ValueError('编辑器命令为空。')
    fd, filename = tempfile.mkstemp(prefix='nailong-memory-', suffix='.md')
    path, owner = Path(filename), None
    cancelled = False
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            if os.name == 'nt':
                private_file_permissions(path)
            output.write(markdown)
        owner = OwnedProcess([*command, str(path)], env=command_environment())
        while owner.process.poll() is None:
            await asyncio.sleep(.025)
        if owner.process.returncode:
            raise ValueError('编辑器未正常退出；未保存记忆。')
        if path.stat().st_size > 160_000:
            raise ValueError('编辑内容超过 160 KiB 上限。')
        return path.read_text(encoding='utf-8')
    finally:
        if owner is not None:
            cleanup = asyncio.create_task(asyncio.to_thread(owner.close))
            cancelled = await join_cleanup(cleanup)
        path.unlink(missing_ok=True)
        if cancelled:
            raise asyncio.CancelledError
