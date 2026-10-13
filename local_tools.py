"""Local operations available to the CLI Agent.

File operations normally stay in the selected project; explicit full access is
scoped to one execution. Shell commands are
separately protected by an approval prompt in the agent runtime; they are not
an operating-system sandbox.
"""

import os
import stat
import asyncio
import subprocess
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from nailong.tools.results import HeadTailBuffer, MAX_ARTIFACT_CHARS


PROJECT_ROOT = Path(__file__).resolve().parent
PROTECTED_NAMES = {".git", ".venv", "__pycache__"}
MAX_FILE_CHARS = 12_000
MAX_OUTPUT_CHARS = 12_000
MAX_CAPTURE_BYTES = MAX_OUTPUT_CHARS * 4 + 8
MAX_WRITE_CHARS = 120_000
MAX_SEARCH_FILES = 2_000
MAX_SEARCH_FILE_CHARS = 200_000
_active_project_root: ContextVar[Path | None] = ContextVar("local_tool_project_root", default=None)
_command_result_archive: ContextVar[object | None] = ContextVar("command_result_archive", default=None)
@dataclass
class _FileAccessGrant:
    root: Path
    active: bool = True


_unrestricted_file_root: ContextVar[_FileAccessGrant | None] = ContextVar("unrestricted_file_root", default=None)


def selected_project_root() -> Path:
    return Path(_active_project_root.get() or PROJECT_ROOT).resolve()


@contextmanager
def use_project_root(root: Path):
    """Select one session's file-tool root and restore the previous root on exit."""
    token = _active_project_root.set(Path(root).resolve())
    try:
        yield
    finally:
        _active_project_root.reset(token)


def file_access_is_unrestricted(root: Path | None = None) -> bool:
    """A scope grants access only to tools belonging to its selected project."""
    grant = _unrestricted_file_root.get()
    return bool(grant and grant.active and grant.root == Path(root or selected_project_root()).resolve())


@contextmanager
def use_file_access(root: Path, *, unrestricted: bool = False):
    """Override this task's file boundary, including for a nested default execution."""
    grant = _FileAccessGrant(Path(root).resolve()) if unrestricted else None
    token = _unrestricted_file_root.set(grant)
    try:
        yield
    finally:
        # ContextVars are copied to child tasks. Revoke this grant when its owning
        # scope ends, even if an unjoined task retained the copied context.
        if grant is not None:
            grant.active = False
        _unrestricted_file_root.reset(token)


def display_file_path(path: Path, root: Path | None = None) -> str:
    root = Path(root or selected_project_root()).resolve()
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


def open_regular_file(path: Path, *, binary: bool = True):
    """Open a previously resolved path without following replaced path components."""
    path = Path(path)
    if os.name == "nt":
        from nailong.core.safe_files import open_regular_file as safe_open
        return safe_open(path, binary=binary)
    if not path.is_absolute() or not hasattr(os, 'O_NOFOLLOW') or os.open not in os.supports_dir_fd:
        raise OSError('当前平台无法安全打开文件路径。')
    parent = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    descriptor = None
    try:
        for component in path.parts[1:-1]:
            following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = following
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError('目标不是普通文件。')
        stream = os.fdopen(descriptor, 'rb' if binary else 'r', **({} if binary else {'encoding':'utf-8'}))
        descriptor = None
        return stream
    finally:
        os.close(parent)
        if descriptor is not None:
            os.close(descriptor)


def _is_protected(path: Path) -> bool:
    return any(_is_protected_component(part) for part in path.parts)


def _is_protected_component(name: str) -> bool:
    folded = name.casefold()
    protected_names = {item.casefold() for item in PROTECTED_NAMES}
    return folded in protected_names or folded == ".env" or folded.startswith(".env.")


def resolve_project_path(path: str, allow_missing: bool = False) -> Path:
    """Resolve with the current execution's project and access scope."""
    return resolve_file_path(path, selected_project_root(), allow_missing=allow_missing)


def resolve_file_path(path: str, root: Path, *, allow_missing: bool = False) -> Path:
    root = Path(root).resolve()
    unrestricted = file_access_is_unrestricted(root)
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate

    if os.name == "nt":
        from nailong.core.safe_files import validate_windows_path, is_link_or_reparse
        # Normalize ordinary . / .. paths only after rejecting namespace aliases.
        from pathlib import PureWindowsPath
        raw = os.fspath(path)
        if PureWindowsPath(raw).drive and not PureWindowsPath(raw).is_absolute():
            raise ValueError("Windows 路径不能使用盘符相对形式。")
        if raw.startswith(("\\\\", "//")) or ":" in raw[2:]:
            raise ValueError("Windows 路径不能使用网络、设备命名空间或备用数据流。")
        # Validate each real component before abspath discards relative navigation.
        for component in candidate.parts[1:]:
            if component not in {".", ".."}:
                validate_windows_path(str(Path(candidate.anchor) / component))
        candidate = Path(os.path.abspath(candidate))
        validate_windows_path(candidate)
        for ancestor in (*reversed(candidate.parents), candidate):
            if is_link_or_reparse(ancestor):
                raise ValueError("文件路径不能被链接或重解析点重定向。")

    lexical_relative = candidate.relative_to(root) if candidate.is_relative_to(root) else candidate
    if not unrestricted and _is_protected(lexical_relative):
        raise ValueError("该路径属于密钥或项目内部目录，不能通过文件工具访问。")

    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise ValueError("路径无法解析。") from error

    if not unrestricted and not resolved.is_relative_to(root):
        raise ValueError("只能访问项目根目录以内的路径。")
    if not unrestricted and _is_protected(resolved.relative_to(root)):
        raise ValueError("该路径属于密钥或项目内部目录，不能通过文件工具访问。")
    if not allow_missing and not resolved.exists():
        raise FileNotFoundError("文件或目录不存在。")
    return resolved


def is_exact_project_path(path: str, expected_path: str) -> bool:
    """Check an exact normalized project path and reject symlink redirection."""
    requested = Path(path)
    expected = Path(expected_path)
    if not requested.is_absolute():
        requested = selected_project_root() / requested
    if not expected.is_absolute():
        expected = selected_project_root() / expected

    requested_lexical = Path(os.path.abspath(requested))
    expected_lexical = Path(os.path.abspath(expected))
    if requested_lexical != expected_lexical:
        return False
    try:
        resolved = resolve_project_path(str(requested_lexical), allow_missing=True)
    except ValueError:
        return False
    return resolved == expected_lexical


def _safe_limit(limit: int, maximum: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return maximum
    return max(1, min(value, maximum))


def _iter_files(start: Path) -> Iterator[Path]:
    from nailong.tools.files import FileSession
    yield from FileSession(selected_project_root())._safe_files(start)[0]


def list_files(path: str = ".", limit: int = 100) -> dict:
    """List project files under a relative path, skipping protected folders."""
    try:
        target = resolve_project_path(path)
        if not target.is_dir() and not target.is_file():
            return {"ok": False, "error": "目标不是文件或目录。"}

        maximum = _safe_limit(limit, 100)
        files = []
        truncated = False
        for file_path in _iter_files(target):
            if len(files) == maximum:
                truncated = True
                break
            files.append(display_file_path(file_path))
        return {"ok": True, "files": files, "truncated": truncated}
    except FileNotFoundError as error:
        return {"ok": False, "error": str(error)}
    except ValueError as error:
        return {"ok": False, "error": str(error)}


def read_file(path: str, max_chars: int = MAX_FILE_CHARS) -> dict:
    """Read a UTF-8 project file, capped at 12,000 characters."""
    try:
        target = resolve_project_path(path)
        if not target.is_file():
            return {"ok": False, "error": "目标不是普通文件。"}
        maximum = _safe_limit(max_chars, MAX_FILE_CHARS)
        with open_regular_file(target, binary=False) as source:
            content = source.read(maximum + 1)
        truncated = len(content) > maximum
        return {
            "ok": True,
            "path": display_file_path(target),
            "content": content[:maximum],
            "truncated": truncated,
        }
    except FileNotFoundError as error:
        return {"ok": False, "error": str(error)}
    except UnicodeDecodeError:
        return {"ok": False, "error": "文件不是有效的 UTF-8 文本。"}
    except (IsADirectoryError, OSError):
        return {"ok": False, "error": "文件无法读取。"}
    except ValueError as error:
        return {"ok": False, "error": str(error)}


def search_text(query: str, path: str = ".", limit: int = 50) -> dict:
    """Use the same literal-search and coverage contract as the registered tool."""
    from nailong.tools.files import FileSession
    return FileSession(selected_project_root()).search_text(query, path, limit)


def write_file(path: str, content: str, *, file_session=None) -> dict:
    """Safe primitive; replacing an existing file requires its reading session."""
    from nailong.tools.files import FileSession
    session = file_session or FileSession(selected_project_root())
    if session.project_root != selected_project_root():
        return {"ok": False, "error": "写入会话不属于当前项目。"}
    return session.write_file(path, content)


def _command_environment() -> dict:
    """Avoid passing the model credential to child shell processes."""
    return {key: value for key, value in os.environ.items() if key != "DEEPSEEK_API_KEY"}


@contextmanager
def use_command_result_archive(archive):
    """Use the execution context's existing output capability, then restore its scope."""
    token = _command_result_archive.set(archive)
    try:
        yield
    finally:
        _command_result_archive.reset(token)


class _CommandOutput:
    """Stream-redact before bounding; retain at most the existing artifact budget."""

    def __init__(self):
        self.archive = _command_result_archive.get()
        self.preview = HeadTailBuffer(MAX_OUTPUT_CHARS)
        self.parts = []
        self.archived_chars = 0
        self.total_chars = 0
        self.redacted_chars = 0
        self.pending = ""
        self.secrets = sorted({secret for secret in (
            os.environ.get("DEEPSEEK_API_KEY", ""), getattr(self.archive, "api_key", "")) if secret},
            key=len, reverse=True)
        self.overlap = max((len(secret) - 1 for secret in self.secrets), default=0)

    def append(self, text):
        self.total_chars += len(text)
        text = self.pending + text
        for secret in self.secrets:
            text = text.replace(secret, "[密钥已隐藏]")
        split = max(0, len(text) - self.overlap)
        self.pending = text[split:]
        self._store(text[:split])

    def _store(self, text):
        self.preview.append(text)
        self.redacted_chars += len(text)
        if self.archive is not None:
            retained = text[:max(0, MAX_ARTIFACT_CHARS - self.archived_chars)]
            if retained:
                self.parts.append(retained)
                self.archived_chars += len(retained)

    def result(self):
        self._store(self.pending)
        self.pending = ""
        metadata = {"artifact_complete": False, "artifact_chars": self.archived_chars,
                    "original_chars": self.redacted_chars}
        if self.archive is None:
            metadata["artifact_error"] = "当前执行上下文未配置完整输出归档。"
        else:
            # An extra character lets ResultArchive retain its own incomplete
            # flag for paging, rather than declaring our bounded prefix complete.
            content = "".join(self.parts)
            if self.redacted_chars > self.archived_chars:
                content += " "
            try:
                metadata.update(self.archive.save(content))
                metadata["original_chars"] = self.redacted_chars
            except (OSError, ValueError):
                metadata["artifact_error"] = "完整输出未能归档。"
        return {"output": self.preview.value(), "output_truncated": self.preview.truncated,
                "output_chars": self.total_chars, "output_archive": metadata}


def _start_command_capture(invocation, mode):
    from nailong.core.process_io import PipeCapture
    output = _CommandOutput()
    # The output callback owns the redacted preview and archive. Keep only a
    # single unused character in PipeCapture's ordinary preview buffer.
    capture = PipeCapture(invocation, shell=mode == "shell", cwd=selected_project_root(),
                          env=_command_environment(), max_output_chars=1, on_stdout=output.append)
    return capture, output


def _command_failure(message, expected_exit_code, *, code="invalid_parameters"):
    return {"ok": False, "error": message, "error_code": code, "started": False,
            "completed": False, "exit_code": None, "exit_code_matches": False,
            "expected_exit_code": expected_exit_code if type(expected_exit_code) is int else None,
            "timed_out": False, "cancelled": False, "output": "", "output_truncated": False}


def _command_input(command, argv, expected_exit_code):
    if type(expected_exit_code) is not int:
        return None, "expected_exit_code 必须是整数。"
    if not isinstance(command, str) or "\x00" in command:
        return None, "command 必须是不含空字符的字符串。"
    if argv is not None:
        if command != "":
            return None, "command 与 argv 只能提供一个。"
        if not isinstance(argv, (list, tuple)) or not 1 <= len(argv) <= 256:
            return None, "argv 必须是包含 1–256 个字符串的数组。"
        if any(not isinstance(item, str) or "\x00" in item for item in argv) or not argv[0].strip():
            return None, "argv 必须包含有效的程序名及不含空字符的字符串参数。"
        if sum(len(item) + 1 for item in argv) > 4_000:
            return None, "argv 总长度不能超过 4,000 个字符。"
        return list(argv), None
    if not command.strip():
        return None, "命令不能为空。"
    if len(command) > 4_000:
        return None, "命令不能超过 4,000 个字符。"
    return command, None


def _command_result(capture, output, expected_exit_code, mode, *, timed_out=False, cancelled=False):
    exit_code = capture.process.returncode
    matched = exit_code == expected_exit_code
    return {"ok": not timed_out and not cancelled and matched,
            "started": True, "completed": not timed_out and not cancelled,
            "execution_mode": mode, "shell": mode == "shell",
            "exit_code": exit_code, "expected_exit_code": expected_exit_code,
            "exit_code_matches": matched, "timed_out": timed_out, "cancelled": cancelled,
            **output.result()}


def _capture_command_output(command, timeout, *, expected_exit_code=0, with_metadata=False):
    """Keep the legacy capture tuple while allowing the adapter to retain metadata."""
    mode = "shell" if isinstance(command, str) else "argv"
    capture, output = _start_command_capture(command, mode)
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        while not capture.finished:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(.01)
    finally:
        capture.close()
    if capture.errors:
        raise capture.errors[0]
    result = _command_result(capture, output, expected_exit_code, mode, timed_out=timed_out)
    if with_metadata:
        return result
    return result["output"], result["output_truncated"], result["timed_out"], result["exit_code"]


def run_command(command: str = "", timeout_seconds: int = 30, *, argv=None,
                expected_exit_code: int = 0) -> dict:
    """Run an approved shell string or literal argv; output never attests test execution."""
    invocation, error = _command_input(command, argv, expected_exit_code)
    if error:
        return _command_failure(error, expected_exit_code)
    try:
        timeout = _safe_limit(timeout_seconds, 30)
    except OverflowError:
        return _command_failure("timeout_seconds 必须是有限数值。", expected_exit_code)
    try:
        return _capture_command_output(invocation, timeout, expected_exit_code=expected_exit_code,
                                       with_metadata=True)
    except (OSError, subprocess.SubprocessError):
        return _command_failure("命令未能启动。", expected_exit_code, code="command_start_failed")


async def run_command_async(command: str = "", timeout_seconds: int = 30, *, argv=None,
                            expected_exit_code: int = 0) -> dict:
    """Cancellable equivalent; join owned process cleanup before propagating cancellation."""
    from nailong.core.process_io import join_cleanup
    from nailong.core.processes import ProcessCancelled
    invocation, error = _command_input(command, argv, expected_exit_code)
    if error:
        return _command_failure(error, expected_exit_code)
    try:
        timeout = _safe_limit(timeout_seconds, 30)
    except OverflowError:
        return _command_failure("timeout_seconds 必须是有限数值。", expected_exit_code)
    mode = "argv" if argv is not None else "shell"
    try:
        capture, output = _start_command_capture(invocation, mode)
    except (OSError, subprocess.SubprocessError):
        return _command_failure("命令未能启动。", expected_exit_code, code="command_start_failed")
    deadline = time.monotonic() + timeout
    timed_out, cancelled = False, False
    try:
        while not capture.finished:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            await asyncio.sleep(.01)
    except asyncio.CancelledError:
        cancelled = True
    finally:
        cleanup = asyncio.create_task(asyncio.to_thread(capture.close))
        cancelled = await join_cleanup(cleanup) or cancelled
    if capture.errors:
        raise capture.errors[0]
    result = _command_result(capture, output, expected_exit_code, mode,
                             timed_out=timed_out, cancelled=cancelled)
    if cancelled:
        raise ProcessCancelled(result)
    return result
