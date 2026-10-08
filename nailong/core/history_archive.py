"""Private, immutable snapshots of redacted messages and tool arguments."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, message_to_dict
from nailong.core.safe_files import atomic_write_bytes, open_regular_file, pinned_directory, private_directory_permissions


MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_PAGE_CHARS = 6_000
_METADATA_BYTES = 128 * 1024
_THREAD_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_REFERENCE = re.compile(r"^hist_[a-f0-9]{64}$")
_REDACTED = "[密钥已隐藏]"
_DIRECTORY_FLAGS = (os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW) if os.name != 'nt' else 0


class _WindowsDirectory:
    def __init__(self, path: Path, *, create: bool):
        self.path = path
        self.context = pinned_directory(path, create=create)
        self.context.__enter__()

    def close(self):
        self.context.__exit__(None, None, None)


def _close_directory(directory):
    if isinstance(directory, _WindowsDirectory):
        directory.close()
    else:
        os.close(directory)


def _encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")


def _text(content: Any) -> str:
    return content if isinstance(content, str) else _encode(content).decode("utf-8")


def _fit_json(value: Any, budget: int) -> Any:
    """Keep valid JSON within a byte budget; the caller exposes truncation."""
    if len(_encode(value)) <= budget:
        return value
    if isinstance(value, str):
        low, high = 0, len(value)
        while low < high:
            middle = (low + high + 1) // 2
            if len(_encode(value[:middle])) <= budget:
                low = middle
            else:
                high = middle - 1
        return value[:low]
    if isinstance(value, dict):
        result = {}
        remaining = budget - 2
        for key, item in value.items():
            overhead = len(_encode(key)) + 1 + bool(result)
            if remaining - overhead < 4:
                break
            fitted = _fit_json(item, remaining - overhead)
            result[key] = fitted
            remaining -= overhead + len(_encode(fitted))
        return result
    if isinstance(value, list):
        result = []
        remaining = budget - 2
        for item in value:
            available = remaining - bool(result)
            if available < 4:
                break
            fitted = _fit_json(item, available)
            result.append(fitted)
            remaining -= len(_encode(fitted)) + (len(result) > 1)
        return result
    return None


def _open_directory(path: Path, *, create: bool) -> int:
    """Walk with directory handles so neither ancestors nor races follow links."""
    descriptor = os.open(path.anchor, _DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            if part in {".", ".."}:
                raise ValueError("归档路径不能包含相对跳转。")
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            next_descriptor = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        if create:
            os.fchmod(descriptor, 0o700)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class HistoryArchive:
    """Store JSON under a trusted project root, addressed only by opaque IDs.

    Snapshots never refresh from current project files. References include the
    thread identity and complete redacted input, so retries reuse a snapshot and
    changed results receive a new version. Reads raise ValueError for invalid,
    missing, cross-thread, corrupt, or symlink-backed references.
    """

    def __init__(self, root: str | Path, api_key: str = ""):
        # Resolving here would hide a caller-supplied symlink before validation.
        self.root = Path(root).expanduser().absolute()
        self.api_key = api_key

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(self.api_key, _REDACTED) if self.api_key else value
        if isinstance(value, dict):
            return {self._redact(str(key)): self._redact(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._redact(item) for item in value]
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float) and value == value and abs(value) != float("inf"):
            return value
        return self._redact(str(value))

    @staticmethod
    def _thread_key(thread_id: str) -> str:
        if not isinstance(thread_id, str) or not _THREAD_ID.fullmatch(thread_id):
            raise ValueError("会话 ID 格式无效。")
        return hashlib.sha256(thread_id.encode("utf-8")).hexdigest()

    def _thread_directory(self, thread_key: str, *, create: bool) -> int:
        if os.name == 'nt':
            directory = _WindowsDirectory(self.root / thread_key, create=create)
            try:
                if create:
                    private_directory_permissions(self.root)
                    private_directory_permissions(directory.path)
                return directory
            except BaseException:
                directory.close()
                raise
        root_descriptor = _open_directory(self.root, create=create)
        try:
            if create:
                try:
                    os.mkdir(thread_key, mode=0o700, dir_fd=root_descriptor)
                except FileExistsError:
                    pass
            descriptor = os.open(thread_key, _DIRECTORY_FLAGS, dir_fd=root_descriptor)
            if create:
                os.fchmod(descriptor, 0o700)
            return descriptor
        finally:
            os.close(root_descriptor)

    @staticmethod
    def _load(directory: int, reference: str, thread_key: str) -> dict:
        if isinstance(directory, _WindowsDirectory):
            context = open_regular_file(directory.path / (reference + '.json'))
        else:
            descriptor = os.open(reference + ".json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            context = os.fdopen(descriptor, 'rb')
        with context as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("历史归档必须是普通文件。")
            encoded = source.read(MAX_ARCHIVE_BYTES + 1)
        if len(encoded) > MAX_ARCHIVE_BYTES:
            raise ValueError("历史归档超过大小限制。")
        record = json.loads(encoded)
        if not isinstance(record, dict) or record.get("reference") != reference or record.get("thread_key") != thread_key:
            raise ValueError("历史归档与当前会话不匹配。")
        if record.get("schema_version") != 1 or not isinstance(record.get("message"), dict):
            raise ValueError("历史归档格式无效。")
        data = record["message"].get("data")
        if not isinstance(data, dict) or "content" not in data:
            raise ValueError("历史归档缺少消息正文。")
        if not record.get('archive_truncated'):
            identity=_encode({'thread_key':thread_key,'message':record['message'],'arguments':record.get('arguments')})
            if 'hist_'+hashlib.sha256(identity).hexdigest()!=reference:
                raise ValueError('历史归档正文或参数与不可变引用不一致。')
        return record

    @staticmethod
    def _evidence(message: dict, arguments: Any) -> dict | None:
        data = message["data"]
        content = data.get("content", "")
        try:
            payload = json.loads(content) if isinstance(content, str) else None
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return None
        args = arguments if isinstance(arguments, dict) else {}
        path = payload.get("path") or args.get("path")
        if not path or "content" not in payload:
            return None
        line_start = payload.get("line_start", payload.get("start_line", payload.get("offset", args.get("offset"))))
        line_end = payload.get("line_end", payload.get("end_line"))
        if line_end is None and type(line_start) is int and isinstance(payload["content"], str):
            line_end = line_start + max(0, len(payload["content"].splitlines()) - 1)
        result = {
            "historical": True,
            "path": path,
            "line_start": line_start,
            "line_end": line_end,
            "content_sha256": hashlib.sha256(_text(payload["content"]).encode("utf-8")).hexdigest(),
            "source_truncated": bool(payload.get("truncated")),
        }
        if "version" in payload:
            result["source_version"] = payload["version"]
        return result

    def _record(self, thread_key: str, message: BaseMessage, arguments: Any) -> tuple[str, dict]:
        serialized = self._redact(message_to_dict(message))
        arguments = self._redact(arguments)
        identity = _encode({"thread_key": thread_key, "message": serialized, "arguments": arguments})
        reference = "hist_" + hashlib.sha256(identity).hexdigest()
        data = serialized["data"]
        text = _text(data.get("content", ""))
        record = {
            "schema_version": 1,
            "reference": reference,
            "thread_key": thread_key,
            "kind": serialized["type"],
            "name": data.get("name"),
            "message_id": data.get("id"),
            "version": {
                "historical": True,
                "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "archived_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            },
            "evidence": self._evidence(serialized, arguments),
            "archive_truncated": False,
            "original_content_chars": len(text),
            "message": serialized,
            "arguments": arguments,
        }
        if len(_encode(record)) > MAX_ARCHIVE_BYTES:
            record["archive_truncated"] = True
            # Keep the result body available even when arbitrary metadata or tool
            # arguments are huge. Each independent field has a bounded share.
            metadata = {key: value for key, value in data.items() if key != "content"}
            metadata = _fit_json(metadata, _METADATA_BYTES)
            metadata["content"] = data["content"]
            record["message"] = {"type": serialized["type"], "data": metadata}
            record["arguments"] = _fit_json(arguments, _METADATA_BYTES)
            record["arguments_truncated"] = record["arguments"] != arguments
            record["metadata_truncated"] = metadata != data
            for field in ("name", "message_id", "evidence"):
                record[field] = _fit_json(record[field], _METADATA_BYTES)
            content = metadata.pop("content")
            metadata["content"] = ""
            remaining = MAX_ARCHIVE_BYTES - len(_encode(record)) + 2
            metadata["content"] = _fit_json(content, remaining)
        return reference, record

    def save(self, thread_id: str, message: BaseMessage, arguments: Any = None) -> str:
        """Atomically publish one private redacted snapshot, returning its ID."""
        thread_key = self._thread_key(thread_id)
        reference, record = self._record(thread_key, message, arguments)
        try:
            directory = self._thread_directory(thread_key, create=True)
            try:
                try:
                    self._load(directory, reference, thread_key)
                    return reference
                except FileNotFoundError:
                    pass
                if isinstance(directory, _WindowsDirectory):
                    try:
                        atomic_write_bytes(directory.path / (reference + '.json'), _encode(record), replace=False)
                    except FileExistsError:
                        self._load(directory, reference, thread_key)
                    return reference
                temporary = "." + secrets.token_hex(16) + ".tmp"
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        os.fchmod(output.fileno(), 0o600)
                        output.write(_encode(record))
                        output.flush()
                        os.fsync(output.fileno())
                    # Linking a complete temporary file publishes atomically
                    # without replacing a concurrent writer's earlier version.
                    try:
                        os.link(temporary, reference + ".json", src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                    except FileExistsError:
                        self._load(directory, reference, thread_key)
                    os.fsync(directory)
                finally:
                    os.unlink(temporary, dir_fd=directory)
                return reference
            finally:
                _close_directory(directory)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("历史归档路径或记录不可安全访问。") from error

    def load(self, thread_id: str, reference: str) -> dict:
        """Internal bounded complete record; tool responses must still paginate."""
        thread_key = self._thread_key(thread_id)
        if not isinstance(reference, str) or not _REFERENCE.fullmatch(reference):
            raise ValueError("历史归档引用格式无效；不能传入路径。")
        try:
            directory = self._thread_directory(thread_key, create=False)
            try:
                record = self._load(directory, reference, thread_key)
            finally:
                _close_directory(directory)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("当前会话找不到可安全读取的历史归档。") from error
        return record

    def read(self, thread_id: str, reference: str, offset: int = 0, max_chars: int = MAX_PAGE_CHARS) -> dict:
        """Read a historical excerpt; offsets count Unicode characters."""
        if type(offset) is not int or offset < 0 or type(max_chars) is not int or max_chars <= 0:
            raise ValueError("分页 offset 须为非负整数，max_chars 须为正整数。")
        record=self.load(thread_id,reference)
        data = record["message"]["data"]
        content = _text(data["content"])
        excerpt = content[offset:offset + min(max_chars, MAX_PAGE_CHARS)]
        next_offset = offset + len(excerpt)
        truncated = next_offset < len(content)
        # Raw arguments can themselves contain entire file bodies. Return a
        # bounded description so they cannot defeat the page's content limit.
        arguments = _fit_json(record.get("arguments"), 1_024)
        identifiers = {key: _fit_json(value, 256) for key, value in {
            "name": record.get("name"),
            "message_id": record.get("message_id"),
            "tool_call_id": data.get("tool_call_id"),
        }.items()}
        evidence = _fit_json(record.get("evidence"), 1_024)
        metadata_truncated = bool(record.get("metadata_truncated")) or any(
            identifiers[key] != value for key, value in {
                "name": record.get("name"), "message_id": record.get("message_id"), "tool_call_id": data.get("tool_call_id"),
            }.items()
        ) or evidence != record.get("evidence")
        return {
            "reference": reference,
            "kind": record.get("kind"),
            **identifiers,
            "status": data.get("status"),
            "arguments": arguments,
            "arguments_truncated": bool(record.get("arguments_truncated")) or arguments != record.get("arguments"),
            "metadata_truncated": metadata_truncated,
            "version": record.get("version"),
            "evidence": evidence,
            "archive_truncated": bool(record.get("archive_truncated")),
            "original_content_chars": record.get("original_content_chars"),
            "stored_content_chars": len(content),
            "content": excerpt,
            "offset": offset,
            "next_offset": next_offset if truncated else None,
            "truncated": truncated,
        }
