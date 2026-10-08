"""Local operations available to the CLI Agent.

File operations normally stay in the selected project; explicit full access is
scoped to one execution. Shell commands are
separately protected by an approval prompt in the agent runtime; they are not
an operating-system sandbox.
"""

import os
import stat
import codecs
import selectors
import signal
import subprocess
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from nailong.tools.results import HeadTailBuffer


PROJECT_ROOT = Path(__file__).resolve().parent
PROTECTED_NAMES = {".git", ".venv", "__pycache__"}
MAX_FILE_CHARS = 12_000
MAX_OUTPUT_CHARS = 12_000
MAX_CAPTURE_BYTES = MAX_OUTPUT_CHARS * 4 + 8
MAX_WRITE_CHARS = 120_000
MAX_SEARCH_FILES = 2_000
MAX_SEARCH_FILE_CHARS = 200_000
_active_project_root: ContextVar[Path | None] = ContextVar("local_tool_project_root", default=None)
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
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()


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


def _kill_process_group(process: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()


def _capture_command_output(command: str, timeout: int) -> tuple[str, bool, bool, int]:
    if os.name == "nt":
        from nailong.core.process_io import capture_command_output
        return capture_command_output(command, cwd=selected_project_root(), env=_command_environment(),
                                      timeout=timeout, max_output_chars=MAX_OUTPUT_CHARS)
    process = subprocess.Popen(
        command,
        shell=True,
        cwd=selected_project_root(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=_command_environment(),
        start_new_session=(os.name == "posix"),
        bufsize=0,
    )
    output = HeadTailBuffer(MAX_OUTPUT_CHARS)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    timed_out = False
    deadline = time.monotonic() + timeout
    cleanup_deadline = None

    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            now = time.monotonic()
            if not timed_out and now >= deadline:
                timed_out = True
                cleanup_deadline = now + 1.0
                _kill_process_group(process)

            if timed_out:
                remaining = cleanup_deadline - now
                if remaining <= 0:
                    break
                wait_for = min(remaining, 0.1)
            else:
                wait_for = max(0.0, deadline - now)

            events = selector.select(wait_for)
            if not events:
                continue
            for key, _ in events:
                chunk = os.read(key.fd, 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                output.append(decoder.decode(chunk, final=False))

    if process.poll() is None and not timed_out:
        remaining = deadline - time.monotonic()
        if remaining > 0:
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                timed_out = True
        else:
            timed_out = True

    if process.poll() is None:
        _kill_process_group(process)
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

    output.append(decoder.decode(b"", final=True))
    return output.value(), output.truncated, timed_out, process.returncode


def run_command(command: str, timeout_seconds: int = 30) -> dict:
    """Run a shell command in the project after CLI approval; this is not a sandbox."""
    if not command or not command.strip():
        return {"ok": False, "error": "命令不能为空。"}
    if len(command) > 4_000:
        return {"ok": False, "error": "命令不能超过 4,000 个字符。"}
    timeout = _safe_limit(timeout_seconds, 30)
    try:
        output, output_truncated, timed_out, exit_code = _capture_command_output(
            command, timeout
        )
        return {
            "ok": not timed_out and exit_code == 0,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "output": output,
            "output_truncated": output_truncated,
        }
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "error": "命令未能启动。"}
