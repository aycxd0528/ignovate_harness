"""Inline approval prompt that temporarily hands the terminal to prompt_toolkit."""

from __future__ import annotations

import json
import asyncio

from prompt_toolkit import PromptSession
from rich.text import Text

from nailong.core.permissions import ApprovalDecision
from ui.console import Console
from ui.render import render_approval


async def prompt_approval(
    session: PromptSession,
    console: Console,
    action: dict,
    *,
    api_key: str = "",
) -> ApprovalDecision:
    """Ask y/n/a; Enter and unknown input safely resolve to reject."""
    console.pause_live()
    try:
        console.print(render_approval(action, api_key=api_key, theme=console.theme))
        while True:
            answer = await session.prompt_async("  允许执行吗？[y/n/a/d] ", default="n")
            choice = str(answer or "n").strip().lower()
            if choice in {"y", "yes"}:
                return ApprovalDecision("approve_once")
            if choice in {"a", "allow"}:
                return ApprovalDecision("approve_session")
            if choice in {"n", "no", "", "reject"}:
                return ApprovalDecision("reject")
            if choice == "d":
                args = action.get("args", {}) or {}
                detail = json.dumps(args, ensure_ascii=False, indent=2, default=str)
                if api_key:
                    detail = detail.replace(api_key, "[密钥已隐藏]")
                console.print(Text("完整参数："))
                console.print(Text(detail))
                continue
            return ApprovalDecision("reject", comment="输入无效，按拒绝处理。")
    except KeyboardInterrupt:
        raise asyncio.CancelledError('审批已取消。') from None
    finally:
        console.resume_live()
