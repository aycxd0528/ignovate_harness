"""Scoped file exploration and version-checked editing operations."""

from __future__ import annotations

import difflib
import codecs
import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import selectors
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import local_tools
from nailong.core.safe_files import is_link_or_reparse
from nailong.tools.coordination import project_coordinator, active_read_permission


MAX_READ_CHARS = 12_000
MAX_READ_LINES = 1_000
MAX_GLOB_RESULTS = 200
MAX_GREP_RESULTS = 100
MAX_GREP_FILES = 2_000
MAX_GREP_FILE_CHARS = 200_000
_PROTECTED_NAMES = {".git", ".venv", "__pycache__"}


@dataclass(frozen=True)
class FileSnapshot:
    digest: str
    mtime_ns: int
    size: int
    inode: int = 0


class GrepBackendError(RuntimeError):
    """Raised when ripgrep cannot safely complete a requested search."""


class FileSession:
    """Track files read by one tool bundle and reject stale edits."""

    def __init__(self, project_root: str | Path | None = None):
        self.project_root = Path(project_root or local_tools.selected_project_root()).resolve()
        self._snapshots: dict[Path, FileSnapshot] = {}
        self._read_ranges: dict[Path, tuple[str, int, list[tuple[int, int]]]] = {}
        self._snapshot_lock = threading.RLock()
        self._edit_lock = threading.RLock()
        self.mutation_lock = project_coordinator(self.project_root)

    def resolve(self, path: str, *, allow_missing: bool = False) -> Path:
        return local_tools.resolve_file_path(path, self.project_root, allow_missing=allow_missing)

    def display_path(self, path: Path) -> str:
        """External paths remain absolute in results and version bindings."""
        return local_tools.display_file_path(path, self.project_root)

    @staticmethod
    def _is_protected(name: str) -> bool:
        folded = name.casefold()
        return (
            folded in {item.casefold() for item in _PROTECTED_NAMES}
            or folded == ".env"
            or folded.startswith(".env.")
        )

    def _safe_files(self, start: Path, *, limit: int | None = None, skipped=None) -> tuple[list[Path], bool]:
        unrestricted = local_tools.file_access_is_unrestricted(self.project_root)
        def permitted(path):
            policy = active_read_permission.get()
            if policy is None:
                return True
            relative = self.display_path(path)
            decision = policy(relative)
            if decision == "allow":
                return True
            if skipped is not None:
                skipped.append({"path": relative,
                                "reason": "approval_required" if decision == "ask" else "permission_denied"})
            return False
        if start.is_file():
            return ([start], False) if permitted(start) else ([], True)
        paths: list[Path] = []
        truncated = False
        scanned = 0
        visited_directories = set()
        visited_files = set()
        def inaccessible(error):
            nonlocal truncated
            truncated = True
            if skipped is not None:
                skipped.append({"reason": "directory_unreadable"})
        for root, directories, filenames in os.walk(start, followlinks=unrestricted, onerror=inaccessible):
            root_path = Path(root)
            if unrestricted:
                try:
                    canonical = root_path.resolve()
                except (OSError, RuntimeError):
                    inaccessible(None)
                    directories[:] = []
                    continue
                if canonical in visited_directories:
                    directories[:] = []
                    continue
                visited_directories.add(canonical)
            directories[:] = sorted(
                directory
                for directory in directories
                if (not (os.name == "nt" and is_link_or_reparse(root_path / directory))
                    and (unrestricted or (not self._is_protected(directory)
                                          and not (root_path / directory).is_symlink())))
            )
            for filename in sorted(filenames):
                scanned += 1
                if scanned > MAX_GREP_FILES:
                    return paths, True
                candidate = root_path / filename
                try:
                    resolved = self.resolve(str(candidate))
                except (FileNotFoundError, ValueError, OSError):
                    if not self._is_protected(filename):
                        truncated = True
                        if skipped is not None:
                            skipped.append({"reason": "path_unavailable"})
                    continue
                if not resolved.is_file():
                    continue
                if unrestricted and resolved in visited_files:
                    continue
                if not permitted(resolved):
                    truncated = True
                    continue
                if limit is not None and len(paths) >= limit:
                    truncated = True
                    return paths, truncated
                paths.append(resolved)
                visited_files.add(resolved)
        return paths, truncated

    def list_files(self, path=".", limit=100):
        try:
            skipped = []
            paths, truncated = self._safe_files(self.resolve(path), limit=limit, skipped=skipped)
            partial = truncated or bool(skipped)
            return {"ok": True, "files": [self.display_path(item) for item in paths],
                    "truncated": partial, "skipped_files": skipped[:100], "skipped_count": len(skipped),
                    "coverage_complete": not partial, "coverage": "partial" if partial else "complete"}
        except (OSError, ValueError) as error:
            return {"ok": False, "error": str(error)}

    def read_file(
        self,
        path: str,
        offset: int = 1,
        limit: int = 200,
        max_chars: int | None = None,
        char_offset: int = 0,
    ) -> dict[str, Any]:
        """Read a UTF-8 file by 1-based line offset and line limit."""
        try:
            target = self.resolve(path)
            if not target.is_file():
                return {"ok": False, "error": "目标不是普通文件。"}
            try:
                start = int(offset)
                line_limit = int(limit)
                character_start = int(char_offset)
            except (TypeError, ValueError):
                return {"ok": False, "error": "offset、limit 和 char_offset 必须是整数。"}
            if start < 1 or line_limit < 1 or character_start < 0:
                return {"ok": False, "error": "offset 和 limit 必须大于 0，char_offset 不能小于 0。"}
            line_limit = min(line_limit, MAX_READ_LINES)
            cap = MAX_READ_CHARS if max_chars is None else max(1, min(int(max_chars), MAX_READ_CHARS))
            before_stat = target.stat()
            digest = hashlib.sha256()
            decoder = codecs.getincrementaldecoder("utf-8")()
            selected_parts: list[str] = []
            remaining = cap
            current_line = 1
            current_line_chars = 0
            newline_count = 0
            total_bytes = 0
            last_byte = None
            char_truncated = False
            next_offset = start
            next_char_offset = character_start
            document_chars = 0
            selected_start = None

            def consume(text: str) -> None:
                nonlocal remaining, current_line_chars, char_truncated
                nonlocal next_offset, next_char_offset
                nonlocal document_chars, selected_start
                if start <= current_line < start + line_limit:
                    skipped = (
                        max(0, character_start - current_line_chars)
                        if current_line == start
                        else 0
                    )
                    suffix = text[skipped:]
                    if remaining:
                        added = suffix[:remaining]
                        if added and selected_start is None:
                            selected_start = document_chars + skipped
                        selected_parts.append(added)
                        remaining -= len(added)
                        if len(suffix) > len(added):
                            char_truncated = True
                            next_offset = current_line
                            next_char_offset = current_line_chars + skipped + len(added)
                    elif suffix and not char_truncated:
                        char_truncated = True
                        next_offset = current_line
                        next_char_offset = current_line_chars + skipped
                current_line_chars += len(text)
                document_chars += len(text)

            with local_tools.open_regular_file(target) as source:
                while chunk := source.read(64 * 1024):
                    digest.update(chunk)
                    total_bytes += len(chunk)
                    last_byte = chunk[-1]
                    position = 0
                    while position < len(chunk):
                        newline_position = chunk.find(b"\n", position)
                        has_newline = newline_position >= 0
                        end = newline_position + 1 if has_newline else len(chunk)
                        text = decoder.decode(chunk[position:end], final=False)
                        consume(text)
                        if has_newline:
                            newline_count += 1
                            current_line += 1
                            current_line_chars = 0
                        position = end
            consume(decoder.decode(b"", final=True))
            total_lines = newline_count + (1 if total_bytes and last_byte != 10 else 0)
            after_stat = target.stat()
            if (
                before_stat.st_size != after_stat.st_size
                or before_stat.st_mtime_ns != after_stat.st_mtime_ns
                or before_stat.st_ino != after_stat.st_ino
            ):
                self.invalidate_read(target)
                return {"ok": False, "error": "文件在读取期间发生变化，请重新读取。"}
            snapshot = FileSnapshot(
                digest=digest.hexdigest(),
                mtime_ns=after_stat.st_mtime_ns,
                size=after_stat.st_size,
                inode=after_stat.st_ino,
            )
            lines_truncated = total_lines >= start + line_limit
            if not char_truncated:
                next_offset = (
                    start + line_limit
                    if lines_truncated
                    else max(start, total_lines + 1)
                )
                next_char_offset = 0
            selected = "".join(selected_parts)
            with self._snapshot_lock:
                self._snapshots[target] = snapshot
                previous = self._read_ranges.get(target)
                ranges = list(previous[2]) if previous and previous[0] == snapshot.digest else []
                if selected_start is not None:
                    ranges.append((selected_start, selected_start + len(selected)))
                elif document_chars == 0 and start == 1 and character_start == 0:
                    ranges.append((0, 0))
                merged = []
                for begin, end in sorted(ranges):
                    if merged and begin <= merged[-1][1]:
                        merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                    else:
                        merged.append((begin, end))
                self._read_ranges[target] = (snapshot.digest, document_chars, merged)
                complete = bool(merged and merged[0] == (0, document_chars))
            return {
                "ok": True,
                "path": self.display_path(target),
                "content": selected,
                # Absolute character position in the decoded file, not a line-local offset.
                "content_offset": selected_start if document_chars else 0,
                "offset": start,
                "next_offset": next_offset,
                "next_char_offset": next_char_offset,
                "truncated": lines_truncated or char_truncated,
                "read_complete": complete,
                "total_chars": document_chars,
                "version": snapshot.digest,
                "coverage": "complete" if complete else "partial",
            }
        except FileNotFoundError as error:
            return {"ok": False, "error": str(error)}
        except UnicodeDecodeError:
            return {"ok": False, "error": "文件不是有效的 UTF-8 文本。"}
        except (OSError, TypeError, ValueError) as error:
            return {"ok": False, "error": str(error) if isinstance(error, ValueError) else "文件无法读取。"}

    def invalidate_read(self, path) -> None:
        try:
            target = self.resolve(str(path), allow_missing=True)
        except (OSError, ValueError):
            return
        with self._snapshot_lock:
            self._snapshots.pop(target, None)
            self._read_ranges.pop(target, None)

    def mutation_version(self, path: str, *, require_complete=False) -> dict:
        """Bind an approval to a resolved path and its content; never treat a hash as full reading."""
        target = self.resolve(path, allow_missing=True)
        relative = self.display_path(target)
        if not target.exists():
            return {"path": relative, "exists": False}
        if not target.is_file():
            raise ValueError("写入目标不是普通文件。")
        with self._snapshot_lock:
            snapshot = self._snapshots.get(target)
            coverage = self._read_ranges.get(target)
        if snapshot is None:
            raise ValueError("请先读取该文件再修改。")
        if require_complete and not (coverage and coverage[0] == snapshot.digest
                                     and coverage[2] and coverage[2][0] == (0, coverage[1])):
            raise ValueError("完整覆盖已有文件前必须完整读取同一版本的全部内容。")
        version = {"path": relative, "exists": True, "digest": snapshot.digest,
                   "size": snapshot.size, "mtime_ns": snapshot.mtime_ns, "inode": snapshot.inode}
        self.check_version(path, version)
        return version

    def check_version(self, path: str, version: dict) -> None:
        target = self.resolve(path, allow_missing=True)
        if version.get("path") != self.display_path(target):
            raise ValueError("文件路径在审批期间发生变化，请重新读取和审批。")
        if not version.get("exists"):
            if target.exists():
                raise ValueError("待创建文件在审批期间已出现，请重新读取和审批。")
            return
        if not target.is_file():
            raise ValueError("文件在审批期间发生变化，请重新读取和审批。")
        before = target.stat()
        digest = hashlib.sha256()
        with (local_tools.open_regular_file(target) if os.name == "nt" else target.open("rb")) as source:
            for chunk in iter(lambda: source.read(65536), b""):
                digest.update(chunk)
        after = target.stat()
        metadata = (after.st_size, after.st_mtime_ns, after.st_ino)
        if (before.st_size, before.st_mtime_ns, before.st_ino) != metadata or digest.hexdigest() != version.get("digest"):
            self.invalidate_read(target)
            raise ValueError("文件在读取或审批后被外部修改，请重新读取和审批。")
        if version.get("inode") and after.st_ino != version["inode"]:
            self.invalidate_read(target)
            raise ValueError("文件在审批期间被替换，请重新读取和审批。")

    def write_file(self, path: str, content: str, *, expected_version=None) -> dict:
        """Create atomically or replace only a fully read, unchanged version."""
        if not isinstance(content, str) or len(content) > local_tools.MAX_WRITE_CHARS:
            return {"ok": False, "error": "写入内容必须是至多 120,000 字符的文本。"}
        try:
            with self.mutation_lock, self._edit_lock:
                target = self.resolve(path, allow_missing=True)
                version = expected_version or self.mutation_version(path, require_complete=True)
                self.check_version(path, version)
                # Even an externally supplied expected hash cannot replace the full-read prerequisite.
                if target.exists():
                    self.mutation_version(path, require_complete=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                if self.resolve(path, allow_missing=True) != target:
                    raise ValueError("文件路径发生变化，请重新读取。")
                self.check_version(path, version)
                mode = target.stat().st_mode if target.exists() else 0o644
                self._atomic_write(target, content.encode("utf-8"), mode, create_only=not version["exists"])
                self.invalidate_read(target)
                return {"ok": True, "path": self.display_path(target),
                        "chars_written": len(content), "version": hashlib.sha256(content.encode("utf-8")).hexdigest()}
        except (OSError, ValueError) as error:
            return {"ok": False, "error": str(error) if isinstance(error, ValueError) else "文件无法写入。"}

    def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        with self.mutation_lock, self._edit_lock:
            return self._edit_file(path, old_string, new_string, replace_all)

    def _edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        """Replace one exact or uniquely normalized text match atomically."""
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            return {"ok": False, "error": "old_string 和 new_string 必须是文本。"}
        if not old_string:
            return {"ok": False, "error": "old_string 不能为空。"}
        try:
            target = self.resolve(path)
            if not target.is_file():
                return {"ok": False, "error": "目标不是普通文件。"}
            snapshot = self._snapshots.get(target)
            if snapshot is None:
                return {
                    "ok": False,
                    "error": "请先读取该文件再编辑。",
                    "hint": "read_file",
                }
            raw = self._read_bytes(target)
            before = raw.decode("utf-8")
            if hashlib.sha256(raw).hexdigest() != snapshot.digest:
                self.invalidate_read(target)
                return {"ok": False, "error": "文件在读取后被外部修改，请重新读取。"}
            if old_string == new_string:
                return {
                    "ok": True,
                    "path": self.display_path(target),
                    "replacements": 0,
                    "line_start": 0,
                    "line_end": 0,
                    "diff": "",
                    "fuzzy": False,
                }

            exact_positions = self._find_positions(before, old_string)
            fuzzy = False
            if exact_positions:
                if len(exact_positions) > 1 and not replace_all:
                    lines = [self._line_number(before, position) for position in exact_positions]
                    return {
                        "ok": False,
                        "error": f"匹配不唯一，匹配行：{', '.join(map(str, lines))}。设置 replace_all 才会全部替换。",
                        "line_numbers": lines,
                    }
                ranges = [(position, position + len(old_string)) for position in exact_positions]
            else:
                normalized_before, spans = self._normalize_with_spans(before)
                normalized_old, _ = self._normalize_with_spans(old_string)
                if not normalized_old:
                    return {"ok": False, "error": "old_string 不能只包含空白字符。"}
                normalized_positions = self._find_positions(normalized_before, normalized_old)
                if len(normalized_positions) != 1:
                    reason = "未找到匹配" if not normalized_positions else "匹配不唯一"
                    return {"ok": False, "error": f"{reason}，请重新读取文件并提供精确片段。"}
                start = normalized_positions[0]
                end = start + len(normalized_old)
                ranges = [(spans[start][0], spans[end - 1][1])]
                fuzzy = True

            newline = self._newline_style(before)
            replacement = self._normalize_newlines(new_string, newline)
            after = before
            for start, end in reversed(ranges):
                after = after[:start] + replacement + after[end:]
            after = self._preserve_final_newline(before, after, newline)
            first_start = ranges[0][0]
            last_end = ranges[-1][1]
            diff = "\n".join(
                difflib.unified_diff(
                    before.splitlines(),
                    after.splitlines(),
                    fromfile=self.display_path(target),
                    tofile=self.display_path(target),
                    lineterm="",
                )
            )
            if after != before:
                if self.resolve(path) != target:
                    return {"ok": False, "error": "文件路径在编辑期间发生变化，请重新读取。"}
                latest = self._read_bytes(target)
                if hashlib.sha256(latest).hexdigest() != snapshot.digest:
                    self.invalidate_read(target)
                    return {"ok": False, "error": "文件在读取后被外部修改，请重新读取。"}
                self._atomic_write(target, after.encode("utf-8"), target.stat().st_mode)
            new_raw = self._read_bytes(target)
            stat = target.stat()
            self._snapshots[target] = FileSnapshot(
                digest=hashlib.sha256(new_raw).hexdigest(),
                mtime_ns=stat.st_mtime_ns,
                size=stat.st_size,
                inode=stat.st_ino,
            )
            # A local patch does not mean the replacement's whole content was returned to the model.
            self._read_ranges.pop(target, None)
            return {
                "ok": True,
                "path": self.display_path(target),
                "replacements": len(ranges),
                "line_start": self._line_number(before, first_start),
                "line_end": self._line_number(before, max(first_start, last_end - 1)),
                "diff": diff,
                "fuzzy": fuzzy,
            }
        except UnicodeDecodeError:
            return {"ok": False, "error": "文件不是有效的 UTF-8 文本。"}
        except FileNotFoundError as error:
            return {"ok": False, "error": str(error)}
        except (OSError, ValueError) as error:
            return {"ok": False, "error": str(error) if isinstance(error, ValueError) else "文件无法编辑。"}

    def glob(self, pattern: str, path: str = ".", limit: int = 100) -> dict[str, Any]:
        """Match safe project files and sort the newest files first."""
        if not pattern or ".." in PurePosixPath(pattern).parts:
            return {"ok": False, "error": "glob pattern 不能为空或包含父目录跳转。"}
        try:
            target = self.resolve(path)
            if not target.is_dir() and not target.is_file():
                return {"ok": False, "error": "搜索目标不是文件或目录。"}
            maximum = self._bounded_int(limit, MAX_GLOB_RESULTS)
            paths, scanned_truncated = self._safe_files(target)
            matches = []
            for file_path in paths:
                relative = Path(os.path.relpath(file_path, target)).as_posix() if target.is_dir() else file_path.name
                path_obj = PurePosixPath(relative)
                matched = path_obj.match(pattern)
                if pattern.startswith("**/"):
                    matched = matched or path_obj.match(pattern[3:])
                if matched:
                    matches.append((file_path.stat().st_mtime_ns, self.display_path(file_path)))
            matches.sort(key=lambda item: (-item[0], item[1]))
            truncated = scanned_truncated or len(matches) > maximum
            return {"ok": True, "files": [path for _, path in matches[:maximum]], "truncated": truncated,
                    "coverage_complete": not truncated, "coverage": "partial" if truncated else "complete"}
        except FileNotFoundError as error:
            return {"ok": False, "error": str(error)}
        except (OSError, ValueError) as error:
            return {"ok": False, "error": str(error) if isinstance(error, ValueError) else "搜索目标无法读取。"}

    def grep(
        self,
        pattern: str,
        path: str = ".",
        include: str | None = None,
        context: int = 0,
        output_mode: str = "content",
        limit: int = MAX_GREP_RESULTS,
        pattern_mode: str = "regex",
    ) -> dict[str, Any]:
        """Search UTF-8 files with ripgrep when available and a Python fallback."""
        if not pattern:
            return {"ok": False, "error": "搜索内容不能为空。"}
        if output_mode not in {"content", "files_with_matches", "count"}:
            return {"ok": False, "error": "output_mode 必须是 content、files_with_matches 或 count。"}
        if pattern_mode not in {"regex", "literal"}:
            return {"ok": False, "error": "pattern_mode 必须是 regex 或 literal。"}
        if pattern_mode == "regex" and shutil.which("rg") is None:
            return {"ok": False, "error": "正则搜索需要 ripgrep；未改变为字面量匹配。",
                    "error_code": "backend_unavailable", "backend": "unavailable", "pattern_mode": pattern_mode,
                    "coverage": "unknown", "coverage_complete": False}
        try:
            context_lines = max(0, min(int(context), 5))
            maximum = self._bounded_int(limit, MAX_GREP_RESULTS)
            target = self.resolve(path)
            if not target.is_dir() and not target.is_file():
                return {"ok": False, "error": "搜索目标不是文件或目录。"}
            skipped = []
            safe_files, files_truncated = self._safe_files(target, limit=MAX_GREP_FILES, skipped=skipped)
            if include:
                safe_files = [
                    item
                    for item in safe_files
                    if self._matches_include(item, target, include)
                ]
            candidates = safe_files
            scan_stats = {"scanned_files": 0}
            safe_files = []
            for item in candidates:
                try:
                    if item.stat().st_size > MAX_GREP_FILE_CHARS:
                        skipped.append({"path": self.display_path(item), "reason": "file_too_large"})
                    else:
                        safe_files.append(item)
                except OSError:
                    skipped.append({"path": self.display_path(item), "reason": "file_unavailable"})

            if pattern_mode == "literal":
                pairs, match_truncated = self._literal_matches(
                    safe_files, pattern, maximum + 1, skipped=skipped, scan_stats=scan_stats
                )
            else:
                rg_matches = self._ripgrep_matches(safe_files, pattern, maximum + 1,
                                                   scan_stats=scan_stats, skipped=skipped)
                pairs, match_truncated = rg_matches or ([], False)

            unique_pairs = list(dict.fromkeys(pairs))
            truncated = files_truncated or bool(skipped) or match_truncated or len(unique_pairs) > maximum
            unique_pairs = unique_pairs[:maximum]
            by_file: dict[Path, list[int]] = {}
            for file_path, line_number in unique_pairs:
                by_file.setdefault(file_path, []).append(line_number)
            file_paths = sorted(by_file, key=lambda item: self.display_path(item))
            coverage = {"backend": "ripgrep" if pattern_mode == "regex" else "python_literal",
                        "pattern_mode": pattern_mode, "scanned_files": scan_stats["scanned_files"],
                        "candidate_files": len(candidates), "skipped_files": skipped[:100],
                        "skipped_count": len(skipped), "coverage_complete": not truncated,
                        "coverage": "partial" if truncated else "complete"}
            if output_mode == "files_with_matches":
                return {
                    "ok": True,
                    "files": [self.display_path(item) for item in file_paths],
                    "truncated": truncated,
                    **coverage,
                }
            if output_mode == "count":
                counts = {
                    self.display_path(item): len(line_numbers)
                    for item, line_numbers in by_file.items()
                }
                return {
                    "ok": True,
                    "counts": counts,
                    "total_matches": sum(counts.values()),
                    "truncated": truncated,
                    **coverage,
                }

            matches = []
            for file_path, line_numbers in by_file.items():
                try:
                    with local_tools.open_regular_file(file_path, binary=False) as source:
                        lines = source.read().splitlines()
                except (OSError, UnicodeDecodeError):
                    skipped.append({"path": self.display_path(file_path), "reason": "content_unreadable"})
                    continue
                for line_number in sorted(set(line_numbers)):
                    if line_number < 1 or line_number > len(lines):
                        continue
                    matches.append(
                        {
                            "path": self.display_path(file_path),
                            "line_number": line_number,
                            "text": lines[line_number - 1][:500],
                            "context_before": [line[:500] for line in lines[max(0, line_number - context_lines - 1):line_number - 1]],
                            "context_after": [line[:500] for line in lines[line_number:min(len(lines), line_number + context_lines)]],
                            "text_truncated": any(len(line) > 500 for line in lines[max(0, line_number-context_lines-1):line_number+context_lines]),
                        }
                    )
            matches.sort(key=lambda item: (item["path"], item["line_number"]))
            truncated = truncated or bool(skipped) or any(item["text_truncated"] for item in matches)
            coverage.update(skipped_files=skipped[:100], skipped_count=len(skipped),
                            coverage_complete=not truncated, coverage="partial" if truncated else "complete")
            return {"ok": True, "matches": matches, "truncated": truncated, **coverage}
        except GrepBackendError as error:
            return {"ok": False, "error": str(error), "error_code": "search_backend_failed", "coverage": "unknown"}
        except FileNotFoundError as error:
            return {"ok": False, "error": str(error)}
        except (OSError, TypeError, ValueError) as error:
            return {"ok": False, "error": str(error) if isinstance(error, ValueError) else "搜索目标无法读取。"}

    def search_text(self, query: str, path: str = ".", limit: int = 50) -> dict[str, Any]:
        """Literal compatibility entry point with the same explicit coverage contract."""
        return self.grep(query, path=path, limit=self._bounded_int(limit, 50), pattern_mode="literal")

    @staticmethod
    def _bounded_int(value: int, maximum: int) -> int:
        try:
            return max(1, min(int(value), maximum))
        except (TypeError, ValueError):
            return maximum

    @staticmethod
    def _matches_include(file_path: Path, target: Path, pattern: str) -> bool:
        relative = Path(os.path.relpath(file_path, target)).as_posix() if target.is_dir() else file_path.name
        path_obj = PurePosixPath(relative)
        return path_obj.match(pattern) or path_obj.name == pattern or fnmatch.fnmatchcase(path_obj.name, pattern)

    def _literal_matches(self, files: list[Path], query: str, limit: int, *, skipped=None, scan_stats=None) -> tuple[list[tuple[Path, int]], bool]:
        matches: list[tuple[Path, int]] = []
        for file_path in files:
            if scan_stats is not None:
                scan_stats["scanned_files"] += 1
            try:
                if file_path.stat().st_size > MAX_GREP_FILE_CHARS:
                    if skipped is not None:
                        skipped.append({"path": self.display_path(file_path), "reason": "file_too_large"})
                    continue
                with local_tools.open_regular_file(file_path, binary=False) as source:
                    for line_number, line in enumerate(source, start=1):
                        if query in line:
                            matches.append((file_path, line_number))
                            if len(matches) >= limit:
                                return matches, True
            except (OSError, UnicodeDecodeError):
                if skipped is not None:
                    skipped.append({"path": self.display_path(file_path), "reason": "content_unreadable"})
                continue
        return matches, False

    def _ripgrep_matches(self, files: list[Path], pattern: str, limit: int, *, scan_stats=None, skipped=None) -> tuple[list[tuple[Path, int]], bool] | None:
        executable = shutil.which("rg")
        if executable is None or not files:
            return None
        matches: list[tuple[Path, int]] = []
        for batch_start in range(0, len(files), 80):
            batch = files[batch_start:batch_start + 80]
            # rg follows explicitly supplied symlinks. Search private snapshots
            # copied from safely opened descriptors, never the original paths.
            with tempfile.TemporaryDirectory(prefix='ignovate-search-') as directory:
                originals: dict[str, Path] = {}
                for index, item in enumerate(batch):
                    if scan_stats is not None:
                        scan_stats['scanned_files'] += 1
                    try:
                        with local_tools.open_regular_file(item) as source:
                            content = source.read(MAX_GREP_FILE_CHARS + 1)
                        if len(content) > MAX_GREP_FILE_CHARS:
                            if skipped is not None:
                                skipped.append({'path': self.display_path(item), 'reason': 'file_too_large'})
                            continue
                        content.decode('utf-8')
                    except (OSError, UnicodeDecodeError):
                        if skipped is not None:
                            skipped.append({'path': self.display_path(item), 'reason': 'content_unreadable'})
                        continue
                    snapshot = Path(directory) / str(index)
                    snapshot.write_bytes(content)
                    originals[str(snapshot)] = item
                if not originals:
                    continue
                command = [executable, '--json', '--text', '--no-ignore',
                           '--max-columns', '500', '--max-columns-preview',
                           '--max-count', '101', '-e', pattern, '--', *originals]
                try:
                    output, returncode, output_truncated = self._bounded_search_output(command)
                except (OSError, subprocess.SubprocessError) as error:
                    raise GrepBackendError(
                        "ripgrep 未能安全完成搜索；未运行正则回退。"
                    ) from error
            if returncode not in {0, 1} and not output_truncated:
                raise GrepBackendError(
                    "ripgrep 拒绝了该正则或搜索失败；未运行正则回退。"
                )
            for line in output.splitlines():
                try:
                    event = json.loads(line)
                    if event.get("type") != "match":
                        continue
                    data = event["data"]
                    raw_path = data["path"].get("text")
                    line_number = int(data["line_number"])
                    if raw_path is None:
                        continue
                    candidate = originals.get(raw_path)
                    if candidate is not None:
                        matches.append((candidate, line_number))
                    if len(matches) >= limit:
                        return matches, True
                except (ValueError, KeyError, TypeError, OSError):
                    continue
            if output_truncated:
                return matches, True
        return matches, False

    def _bounded_search_output(self, command):
        """Bound subprocess capture before decoding; rg never receives shell interpolation."""
        if os.name == "nt":
            from nailong.core.process_io import capture_bytes
            data, code, truncated = capture_bytes(command, cwd=self.project_root,
                env=local_tools._command_environment(), timeout=5, max_output_bytes=2_000_000)
            return data.decode("utf-8", errors="replace"), code, truncated
        process = subprocess.Popen(command, cwd=self.project_root, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, env=local_tools._command_environment())
        output = bytearray()
        cap = 2_000_000
        deadline = time.monotonic() + 5
        truncated = False
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, 5)
                    for key, _ in selector.select(min(remaining, .1)):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        available = cap - len(output)
                        output.extend(chunk[:available])
                        if len(chunk) > available:
                            truncated = True
                            process.kill()
                            return output.decode("utf-8", errors="replace"), process.wait(timeout=1), True
                returncode = process.wait(timeout=max(.01, deadline-time.monotonic()))
            return output.decode("utf-8", errors="replace"), returncode, truncated
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()

    @staticmethod
    def _find_positions(text: str, needle: str) -> list[int]:
        positions = []
        start = 0
        while True:
            found = text.find(needle, start)
            if found < 0:
                return positions
            positions.append(found)
            start = found + len(needle)

    @staticmethod
    def _line_number(text: str, position: int) -> int:
        return text.count("\n", 0, position) + 1

    @staticmethod
    def _normalize_with_spans(text: str) -> tuple[str, list[tuple[int, int]]]:
        output: list[str] = []
        spans: list[tuple[int, int]] = []
        source_offset = 0
        for raw_line in text.splitlines(keepends=True):
            if raw_line.endswith("\r\n"):
                body, ending = raw_line[:-2], "\r\n"
            elif raw_line.endswith(("\n", "\r")):
                body, ending = raw_line[:-1], raw_line[-1:]
            else:
                body, ending = raw_line, ""
            body_end = source_offset + len(body)
            trimmed_body = body.rstrip(" \t")
            for match in re.finditer(r"[ \t]+|[^ \t]+", trimmed_body):
                if match.group(0)[0] in " \t":
                    output.append(" ")
                    spans.append((source_offset + match.start(), source_offset + match.end()))
                else:
                    for index, character in enumerate(match.group(0)):
                        absolute = source_offset + match.start() + index
                        output.append(character)
                        spans.append((absolute, absolute + 1))
            if ending:
                output.append("\n")
                spans.append((body_end, body_end + len(ending)))
            source_offset += len(raw_line)
        return "".join(output), spans

    @staticmethod
    def _newline_style(text: str) -> str:
        if "\r\n" in text:
            return "\r\n"
        if "\r" in text and "\n" not in text:
            return "\r"
        return "\n"

    @staticmethod
    def _normalize_newlines(text: str, newline: str) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        return normalized.replace("\n", newline)

    @staticmethod
    def _preserve_final_newline(before: str, after: str, newline: str) -> str:
        had_final_newline = before.endswith(("\n", "\r"))
        has_final_newline = after.endswith(("\n", "\r"))
        if had_final_newline and not has_final_newline:
            return after + newline
        if not had_final_newline and has_final_newline:
            return after.rstrip("\r\n")
        return after

    @staticmethod
    def _read_bytes(target: Path) -> bytes:
        if os.name == "nt":
            with local_tools.open_regular_file(target) as source:
                return source.read()
        return target.read_bytes()

    @staticmethod
    def _atomic_write(target: Path, content: bytes, mode: int, *, create_only=False) -> None:
        if os.name == "nt":
            from nailong.core.safe_files import atomic_write_bytes
            atomic_write_bytes(target, content, replace=not create_only, private=False)
            return
        temporary_path = None
        try:
            temporary_path = target.parent / f'.{target.name}.{uuid.uuid4().hex}'
            descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                 0o666 if create_only else 0o600)
            with os.fdopen(descriptor, 'wb') as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            if not create_only:
                os.chmod(temporary_path, stat.S_IMODE(mode))
            if create_only:
                # Atomic no-overwrite creation: a file appearing after approval is preserved.
                os.link(temporary_path, target)
            else:
                os.replace(temporary_path, target)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
