"""Project-isolated durable session metadata and redacted JSONL event logs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_NEVER_LOG_KEYS = {
    "api_key",
    "arguments",
    "args",
    "content",
    "old_string",
    "new_string",
    "tool_calls",
    "DEEPSEEK_API_KEY",
}
_MAX_EVENT_TEXT = 12_000


class StreamingRedactor:
    """Redact a configured key even when it is split across token chunks."""

    def __init__(self, secret: str, replacement: str = "[密钥已隐藏]"):
        self.secret = secret
        self.replacement = replacement
        self._pending = ""

    def feed(self, text: str, *, final: bool = False) -> str:
        if not self.secret:
            return text
        combined = self._pending + text
        stable_end = len(combined) if final else max(0, len(combined) - len(self.secret) + 1)
        output: list[str] = []
        cursor = 0
        while cursor < stable_end:
            found = combined.find(self.secret, cursor)
            if found < 0 or found >= stable_end:
                output.append(combined[cursor:stable_end])
                cursor = stable_end
                break
            output.append(combined[cursor:found])
            output.append(self.replacement)
            cursor = found + len(self.secret)
            if not final and cursor > stable_end:
                stable_end = cursor
        self._pending = combined[cursor:]
        if final and self._pending:
            output.append(self._pending.replace(self.secret, self.replacement))
            self._pending = ""
        return "".join(output)


class ProjectSessionStore:
    """Store one project's LangGraph DB and safe event logs outside the repo."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        base_dir: str | Path | None = None,
        api_key: str = "",
    ):
        self.project_root = Path(project_root).resolve()
        if base_dir is None:
            configured = os.environ.get("NAILONG_DATA_DIR")
            base_dir = Path(configured).expanduser() if configured else Path.home() / ".nailong"
        base = Path(base_dir).expanduser().resolve()
        project_key = hashlib.sha1(str(self.project_root).encode("utf-8")).hexdigest()[:12]
        self.root = base / "projects" / project_key
        self.sessions_dir = self.root / "sessions"
        self._database_path = self.root / "checkpoints.sqlite"
        self.api_key = api_key
        self._lock = threading.RLock()

    @property
    def database_path(self) -> Path:
        return self._database_path

    def session_path(self, thread_id: str) -> Path:
        if not isinstance(thread_id, str) or not _SESSION_ID.fullmatch(thread_id):
            raise ValueError("会话 ID 格式无效。")
        return self.sessions_dir / f"{thread_id}.jsonl"

    def ensure_directories(self) -> None:
        if os.name == 'nt':
            from nailong.core.safe_files import pinned_directory, private_directory_permissions
            for directory in (self.root, self.sessions_dir):
                with pinned_directory(directory, create=True):
                    private_directory_permissions(directory)
            return
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.sessions_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.root, 0o700)
            os.chmod(self.sessions_dir, 0o700)
        except OSError:
            pass

    def append_event(
        self,
        thread_id: str,
        kind: str,
        data: dict[str, Any],
        *,
        timestamp: str | None = None,
    ) -> None:
        path = self.session_path(thread_id)
        self.ensure_directories()
        if kind == "turn_start" and isinstance(data.get("message_ids"), (list, tuple)):
            data = {**data, "boundary_message_count": len(data["message_ids"])}
        record = {
            "thread_id": thread_id,
            "cwd": str(self.project_root),
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kind": str(kind),
            "data": self._sanitize(data),
        }
        encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                view = memoryview(encoded)
                while view:
                    written = os.write(descriptor, view)
                    view = view[written:]
            finally:
                os.close(descriptor)

    def _metadata(self):
        from nailong.core.preferences import read_config
        try: return read_config(self.root / 'session-metadata.json', self.root)
        except (ValueError, OSError): return {}

    def rename(self, thread_id, name):
        from nailong.core.preferences import atomic_json
        self.session_path(thread_id)
        if isinstance(name,str): name=''.join(char for char in name if char.isprintable()).strip()
        if not isinstance(name,str) or not 1 <= len(name) <= 80:
            raise ValueError('会话名称须为 1–80 个可显示字符。')
        with self._lock:
            names=self._metadata()
            names[thread_id]=self._sanitize(name.strip())
            self.ensure_directories()
            atomic_json(self.root/'session-metadata.json',names,self.root)
        self.append_event(thread_id,'rename',{'name':names[thread_id]})
        return names[thread_id]

    def search(self, query):
        query=str(query).casefold().strip()
        if not query:
            results=self.list_sessions()
            self._session_selection=(query,[row['thread_id'] for row in results])
            return results
        results=[]
        for row in self.list_sessions():
            searchable=' '.join(str(row.get(key,'')) for key in ('thread_id','name','summary'))
            searchable+=' '+ ' '.join(str(event.get('data',{}).get('text','')) for event in self.read_events(row['thread_id']) if event.get('kind')=='user')
            if query in searchable.casefold(): results.append(row)
        self._session_selection=(query,[row['thread_id'] for row in results])
        return results

    def list_sessions(self) -> list[dict[str, str]]:
        if not self.sessions_dir.is_dir():
            return []
        names = self._metadata()
        summaries: dict[str, dict[str, str]] = {}
        for path in self.sessions_dir.glob("*.jsonl"):
            thread_id = path.stem
            if not _SESSION_ID.fullmatch(thread_id):
                continue
            summary = "会话已保存"
            updated = ""
            try:
                with path.open("r", encoding="utf-8") as source:
                    for line in source:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(event, dict) or not isinstance(event.get('data', {}), dict):
                            continue
                        updated = str(event.get("timestamp", updated))
                        if event.get("kind") == "final":
                            text = event.get("data", {}).get("text", "")
                            if isinstance(text, str) and text:
                                summary = text[:160].replace("\n", " ")
            except (OSError, UnicodeDecodeError):
                continue
            summaries[thread_id] = {
                "thread_id": thread_id,
                "name": names.get(thread_id, "") if isinstance(names.get(thread_id, ""), str) else "",
                "updated_at": updated,
                "summary": summary,
            }
        return sorted(
            summaries.values(),
            key=lambda item: (item["updated_at"], item["thread_id"]),
            reverse=True,
        )

    def resolve_session(self, selector: str, *, query: str = "") -> str:
        """Resolve a session id or a one-based index from the recent list."""
        cached=getattr(self,'_session_selection',None)
        if cached is not None and cached[0]==str(query).casefold().strip():
            sessions=[{'thread_id':item} for item in cached[1]]
        else: sessions = self.search(query)
        for session in sessions:
            if session["thread_id"] == selector:
                return selector
        try:
            index = int(selector)
        except (TypeError, ValueError):
            index = 0
        if 1 <= index <= len(sessions):
            return sessions[index - 1]["thread_id"]
        raise ValueError("找不到该会话；请使用 /sessions 查看可用会话。")

    def read_events(self, thread_id: str) -> list[dict[str, Any]]:
        path = self.session_path(thread_id)
        try:
            with path.open("r", encoding="utf-8") as source:
                result = []
                for line in source:
                    try:
                        value = json.loads(line)
                        if isinstance(value, dict) and isinstance(value.get("data", {}), dict):
                            result.append(value)
                    except json.JSONDecodeError:
                        continue
                return result
        except FileNotFoundError:
            return []

    def last_turn_boundary(self, thread_id: str) -> tuple[int, dict[str, Any]] | None:
        events = self.read_events(thread_id)
        for index in range(len(events) - 1, -1, -1):
            data = events[index].get("data") or {}
            if (events[index].get("kind") == "turn_start"
                    and (not isinstance(data, dict) or data.get("visible") is not False)):
                return index, events[index]
        return None

    def truncate_events(self, thread_id: str, through_index: int) -> int:
        """Rewind dialog events while retaining charges from completed calls."""
        path = self.session_path(thread_id)
        with self._lock:
            events = self.read_events(thread_id)
            cutoff = max(0, through_index + 1)
            retained = events[:cutoff]
            for event in events[cutoff:]:
                if event.get("kind") in {"usage", "usage_missing"}:
                    retained.append({**event, "data": {**(event.get("data") or {}), "rewound": True}})
            removed = max(0, len(events) - len(retained))
            self.ensure_directories()
            encoded = "".join(
                json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
                for event in retained
            ).encode("utf-8")
            temporary = path.with_suffix(".jsonl.tmp")
            descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            try:
                view = memoryview(encoded)
                while view:
                    written = os.write(descriptor, view)
                    view = view[written:]
            finally:
                os.close(descriptor)
            os.replace(temporary, path)
        return removed

    def _sanitize(self, value: Any, *, key: str | None = None) -> Any:
        if key and key.casefold() in {item.casefold() for item in _NEVER_LOG_KEYS}:
            return None
        if isinstance(value, str):
            text = value.replace(self.api_key, "[密钥已隐藏]") if self.api_key else value
            return text[:_MAX_EVENT_TEXT]
        if isinstance(value, dict):
            result = {}
            for item_key, item_value in value.items():
                if str(item_key).casefold() in {name.casefold() for name in _NEVER_LOG_KEYS}:
                    continue
                result[str(item_key)[:80]] = self._sanitize(item_value, key=str(item_key))
            return result
        if isinstance(value, (list, tuple)):
            # Message IDs define a state boundary, so display truncation would
            # cause rewind to delete messages from earlier turns.
            items = value if key == "message_ids" else value[:100]
            return [self._sanitize(item) for item in items]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self._sanitize(str(value))
