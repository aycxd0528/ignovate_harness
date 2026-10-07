"""Bounded, redacted tool responses with private, capability-scoped result paging."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from pathlib import Path

MAX_RESULT_CHARS = 16_000
MAX_ARTIFACT_CHARS = 1_000_000
MAX_ARCHIVE_CHARS = 20_000_000
MAX_ARTIFACTS = 128


class HeadTailBuffer:
    """Keep a bounded prefix and suffix without losing the final failure/test summary."""

    def __init__(self, limit=12_000):
        self.limit = limit
        self.head = ""
        self.tail = ""
        self.total = 0

    def append(self, text):
        first = self.limit // 2
        self.head += text[:max(0, first-len(self.head))]
        self.tail = (self.tail + text)[-(self.limit-first):]
        self.total += len(text)

    @property
    def truncated(self):
        return self.total > self.limit

    def value(self):
        if self.truncated:
            marker = "\n[中间日志已截断]\n"
            remaining = self.limit - len(marker)
            first = remaining // 2
            return self.head[:first] + marker + self.tail[-(remaining-first):]
        tail_start = self.total-len(self.tail)
        overlap = max(0, len(self.head)-tail_start)
        return self.head + self.tail[overlap:]


def redact(value, secret: str):
    if isinstance(value, str):
        return value.replace(secret, "[密钥已隐藏]") if secret else value
    if isinstance(value, dict):
        return {str(key): redact(item, secret) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, secret) for item in value]
    return value


def failure(code: str, message: str, *, retryable=False, hint="", **fields) -> dict:
    return {"ok": False, "status": "error", "error": message,
            "error_code": code, "retryable": retryable, "hint": hint, **fields}


def normalize_result(name: str, result) -> dict:
    if not isinstance(result, dict):
        return failure("invalid_result", "工具未返回有效结果对象。")
    value = dict(result)
    value["ok"] = value.get("ok") is True
    value["status"] = "success" if value["ok"] else "error"
    if not value["ok"]:
        message = str(value.get("error") or value.get("message") or "工具执行失败。")
        code = value.get("error_code")
        if not code:
            code = "timeout" if value.get("timed_out") else "command_failed" if name == "run_command" else "tool_failed"
            if "重新读取" in message or "发生变化" in message or "外部修改" in message:
                code = "file_conflict"
            elif "先读取" in message or "完整读取" in message:
                code = "read_required"
            elif "不存在" in message:
                code = "not_found"
        value.update(error=message, error_code=code)
        value.setdefault("retryable", code in {"file_conflict", "read_required"})
        value.setdefault("hint", "重新读取当前文件后核对参数。" if code in {"file_conflict", "read_required"} else "")
    return value


class ResultArchive:
    """Only references created by this context can be read; arbitrary paths are never accepted."""

    def __init__(self, directory=None, *, api_key=""):
        self.directory = Path(directory) if directory is not None else None
        self.api_key = api_key
        self._entries: dict[str, tuple[Path, int, bool]] = {}
        self._size = 0
        self._lock = threading.RLock()

    def _directory(self):
        if self.directory is None:
            self.directory = Path(tempfile.mkdtemp(prefix="nailong-tool-results-"))
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.is_symlink():
            raise ValueError("工具归档目录不能是符号链接。")
        os.chmod(self.directory, 0o700)
        return self.directory

    def save(self, content: str) -> dict:
        content = redact(content, self.api_key)
        complete = len(content) <= MAX_ARTIFACT_CHARS
        text = content[:MAX_ARTIFACT_CHARS]
        with self._lock:
            if len(self._entries) >= MAX_ARTIFACTS or self._size + len(text) > MAX_ARCHIVE_CHARS:
                raise ValueError("本上下文工具归档预算已用尽。")
            reference = uuid.uuid4().hex
            path = self._directory() / f"{reference}.json"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            self._entries[reference] = (path, len(text), complete)
            self._size += len(text)
        return {"reference": reference, "artifact_complete": complete,
                "artifact_chars": len(text), "original_chars": len(content)}

    def read(self, reference: str, offset=0, max_chars=6000) -> dict:
        if not isinstance(reference, str) or reference not in self._entries:
            return failure("unknown_reference", "当前执行上下文没有该工具结果引用。")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            return failure("invalid_parameters", "offset 必须是非负整数。")
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1 <= max_chars <= 6000:
            return failure("invalid_parameters", "max_chars 必须为 1–6000。")
        with self._lock:
            path, length, complete = self._entries[reference]
            if path.is_symlink():
                return failure("archive_changed", "工具归档文件发生变化。")
            try:
                with path.open("r", encoding="utf-8") as stream:
                    # Read bounded chunks, even when the requested offset is large.
                    remaining = min(offset, length)
                    while remaining:
                        skipped = stream.read(min(remaining, 8192))
                        if not skipped:
                            break
                        remaining -= len(skipped)
                    content = stream.read(max_chars)
            except (OSError, UnicodeDecodeError):
                return failure("archive_unavailable", "工具归档无法读取。")
        return {"ok": True, "reference": reference, "content": redact(content, self.api_key),
                "offset": offset, "next_offset": min(offset, length) + len(content),
                "truncated": offset + len(content) < length,
                "artifact_complete": complete, "total_chars": length,
                "coverage": "complete" if complete and offset == 0 and len(content) == length else "partial"}

    def bound(self, result: dict, *, limit=MAX_RESULT_CHARS) -> dict:
        result = redact(result, self.api_key)
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        if len(encoded) <= limit:
            return result
        try:
            artifact = self.save(encoded)
        except (OSError, ValueError):
            artifact = {"artifact_complete": False, "artifact_error": "完整结果未能归档。"}
        # Preserve the business outcome and actionable identifiers before data previews.
        bounded = {key: result[key] for key in (
            "ok", "status", "error", "error_code", "retryable", "hint", "path",
            "exit_code", "timed_out", "cancelled", "started", "replacements", "line_start",
            "line_end", "offset", "next_offset", "next_char_offset", "version",
            "input_fingerprint", "total_matches", "backend", "pattern_mode", "coverage_complete",
        ) if key in result}
        bounded.update(truncated=True, result_truncated=True, coverage="partial", **artifact)
        bounded["coverage_complete"] = False
        bounded["hint"] = "使用 read_tool_result(reference) 分页读取归档；文件/命令证据可能已过期。"
        for key in ("content", "output", "diff"):
            if isinstance(result.get(key), str):
                value = result[key]
                bounded[key] = value[:4000] + ("\n[中间内容已截断]\n" + value[-4000:] if len(value) > 8000 else value[4000:])
                if key == "output":
                    bounded["output_truncated"] = True
        for key in ("files", "matches", "skipped_files", "skipped_reasons"):
            if key in result:
                bounded[key] = result[key][:10]
        while len(json.dumps(bounded, ensure_ascii=False, default=str)) > limit:
            candidates = [key for key, value in bounded.items() if isinstance(value, (str, list, dict)) and key not in {"reference", "error_code", "status"}]
            if not candidates:
                break
            key = max(candidates, key=lambda item: len(json.dumps(bounded[item], ensure_ascii=False, default=str)))
            value = bounded[key]
            if isinstance(value, str) and len(value) > 100:
                half = len(value)//4
                bounded[key] = (value[:half] + "\n[内容已截断]\n" + value[-half:]
                                if key in {"output", "diff", "content"} else value[:len(value)//2])
            else:
                bounded.pop(key)
        return bounded
