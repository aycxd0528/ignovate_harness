"""Read-only approval previews for file mutations."""

from __future__ import annotations

import difflib
from pathlib import Path

from nailong.tools.files import FileSession


def preview_mutation(action: dict, project_root: str | Path) -> dict[str, str]:
    name = str(action.get("name", ""))
    args = action.get("args", {})
    if name not in {"edit_file", "write_file"}:
        return {}
    session = FileSession(project_root)
    try:
        target = session.resolve(str(args.get("path", "")), allow_missing=name == "write_file")
        if name == "edit_file":
            before = target.read_text(encoding="utf-8")
            old_string = args.get("old_string")
            new_string = args.get("new_string")
            if not isinstance(old_string, str) or not old_string:
                return {"path": target.relative_to(session.project_root).as_posix(), "error": "待审批编辑缺少有效的 old_string。"}
            if not isinstance(new_string, str):
                return {"path": target.relative_to(session.project_root).as_posix(), "error": "待审批编辑缺少有效的 new_string。"}
            positions = session._find_positions(before, old_string)
            if not positions:
                normalized_before, spans = session._normalize_with_spans(before)
                normalized_old, _ = session._normalize_with_spans(old_string)
                if not normalized_old:
                    return {"path": target.relative_to(session.project_root).as_posix(), "error": "待审批编辑片段不能为空。"}
                normalized_positions = session._find_positions(normalized_before, normalized_old)
                if len(normalized_positions) != 1:
                    return {"path": target.relative_to(session.project_root).as_posix(), "error": "无法为该编辑生成唯一 diff；执行前仍会再次验证。"}
                start = normalized_positions[0]
                positions = [spans[start][0]]
                end = spans[start + len(normalized_old) - 1][1]
                ranges = [(positions[0], end)]
            elif len(positions) == 1 or bool(args.get("replace_all")):
                ranges = [(position, position + len(old_string)) for position in positions]
            else:
                return {"path": target.relative_to(session.project_root).as_posix(), "error": "该片段匹配多处；执行前会要求明确替换范围。"}
            newline = session._newline_style(before)
            replacement = session._normalize_newlines(new_string, newline)
            after = before
            for start, end in reversed(ranges):
                after = after[:start] + replacement + after[end:]
            after = session._preserve_final_newline(before, after, newline)
        else:
            before = target.read_text(encoding="utf-8") if target.exists() else ""
            after = args.get("content")
            if not isinstance(after, str):
                return {"path": target.relative_to(session.project_root).as_posix(), "error": "待审批写入内容不是文本。"}
        relative = target.relative_to(session.project_root).as_posix()
        diff_lines = difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{relative}" if before else "/dev/null",
            tofile=f"b/{relative}",
            lineterm="",
        )
        diff = "\n".join(diff_lines)
        if len(diff) > 24_000:
            diff = diff[:24_000] + "\n… diff 已截断"
        return {"path": relative, "diff": diff or "（内容没有变化）"}
    except (OSError, UnicodeDecodeError, ValueError) as error:
        return {"path": str(args.get("path", "")), "error": str(error)}
