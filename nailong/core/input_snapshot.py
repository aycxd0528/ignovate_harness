"""Streaming complete input identities, with bounded runtime digest reuse."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import sys
import threading
import time

from nailong.core.safe_files import pinned_directory
from nailong.core.snapshot_inventory import inventory_database
from nailong.tools.files import FileSession

INPUT_FINGERPRINT_VERSION = 2
INPUT_DOMAIN = b'ignovate-input-v2\0'
MAX_SNAPSHOT_SECONDS = 120
MAX_CACHED_FILES = 131_072
MAX_CACHED_BYTES = 128 * 1024 * 1024
_QUIET_NS = 2_000_000_000
_cache = OrderedDict()
_cache_bytes = 0
_cache_lock = threading.Lock()
active_snapshot_cancellation = ContextVar('input_snapshot_cancellation', default=None)


def _version(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _windows_handle_version(handle, mode):
    import ctypes
    from nailong.core._win32_files import BasicInformation, GetInformationEx, information
    data = information(handle)
    basic = BasicInformation()
    if not GetInformationEx(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)):
        raise ctypes.WinError(ctypes.get_last_error())
    # FILE_BASIC_INFO.ChangeTime is the native modification counter; Windows
    # stat().st_ctime is creation time and cannot establish input stability.
    return (data.volume, (data.index_high << 32) | data.index_low, mode,
            (data.size_high << 32) | data.size_low, basic.written * 100, basic.changed * 100)


def _windows_file_version(path, *, directory=False):
    from nailong.core._win32_files import open_handle, CloseHandle, READ_ATTRIBUTES
    handle = open_handle(path, directory=directory, access=READ_ATTRIBUTES)
    try:
        return _windows_handle_version(handle, os.stat(path, follow_symlinks=False).st_mode)
    finally:
        CloseHandle(handle)


class _Deadline:
    def __init__(self):
        self.end = time.monotonic() + MAX_SNAPSHOT_SECONDS
        self.cancellation = active_snapshot_cancellation.get()

    def check(self):
        if self.cancellation is not None and self.cancellation.is_set():
            raise ValueError('完整验证摘要扫描已取消，输入覆盖未完成。')
        if time.monotonic() >= self.end:
            raise ValueError('完整验证摘要扫描超时，输入覆盖未完成。')

    def stopped(self):
        return ((self.cancellation is not None and self.cancellation.is_set())
                or time.monotonic() >= self.end)


@contextmanager
def _inventory_directory(root, root_handle, relative):
    if os.name == 'nt':
        with pinned_directory(root / relative) as handle:
            yield handle
    else:
        descriptor = os.dup(root_handle)
        try:
            for part in relative.parts:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)


def _directory_version(handle, path):
    if os.name == 'nt':
        return _windows_handle_version(handle, os.stat(path, follow_symlinks=False).st_mode)
    return _version(os.fstat(handle))


def _inventory(root, exclusions, deadline, inventory, phase):
    # Only the disk queue grows with the project; recursion and simultaneously
    # pinned POSIX descriptors do not grow with directory depth.
    with pinned_directory(root) as root_handle:
        inventory.directory(phase, Path(), _directory_version(root_handle, root),
                            covered=Path() not in exclusions)
        while (queued := inventory.next_directory(phase)) is not None:
            deadline.check()
            relative, expected = queued
            with _inventory_directory(root, root_handle, relative) as handle:
                if _directory_version(handle, root / relative) != expected:
                    raise ValueError('生成验证摘要期间输入目录被修改。')
                with os.scandir(root / relative if os.name == 'nt' else handle) as source:
                    for entry in source:
                        deadline.check()
                        path = relative / entry.name
                        if (FileSession._is_protected(entry.name)
                                or path.parts[:2] == ('.nailong', 'exports')):
                            continue
                        value = entry.stat(follow_symlinks=False)
                        if getattr(value, 'st_file_attributes', 0) & 0x400:
                            raise ValueError('验证摘要无法覆盖此 Windows 重解析点。')
                        if stat.S_ISDIR(value.st_mode):
                            version = (_windows_file_version(root / path, directory=True)
                                       if os.name == 'nt' else _version(value))
                            inventory.directory(phase, path, version,
                                                covered=path not in exclusions)
                        elif stat.S_ISLNK(value.st_mode):
                            if os.name == 'nt':
                                raise ValueError('验证摘要无法覆盖此 Windows 符号链接。')
                            target = os.readlink(entry.name, dir_fd=handle)
                            inventory.file(phase, path, (*_version(value), target))
                        elif stat.S_ISREG(value.st_mode):
                            inventory.file(phase, path, _windows_file_version(root / path) if os.name == 'nt'
                                           else _version(value))
                        else:
                            raise ValueError('验证摘要包含非普通文件。')
                if _directory_version(handle, root / relative) != expected:
                    raise ValueError('生成验证摘要期间输入目录被修改。')


def _file_digest(root, relative, version, deadline, scan_time):
    global _cache_bytes
    key = str(root / relative)
    # POSIX ctime invalidates restored-mtime edits. Fresh timestamps are not
    # reused across a coarse filesystem timestamp tick. Windows st_ctime is
    # creation time, so it always takes the complete streaming-read path.
    reusable = (os.name != 'nt' and version[1] != 0
                and 0 < version[-1] < scan_time - _QUIET_NS)
    if reusable:
        with _cache_lock:
            cached = _cache.get(key)
            if cached is not None and cached[0] == version:
                _cache.move_to_end(key)
                return cached[1]
    import local_tools
    digest = hashlib.sha256()
    with local_tools.open_regular_file(root / relative) as source:
        def current_version():
            if os.name == 'nt':
                import msvcrt
                return _windows_handle_version(msvcrt.get_osfhandle(source.fileno()),
                    os.stat(root / relative, follow_symlinks=False).st_mode)
            return _version(os.fstat(source.fileno()))
        if current_version() != version:
            raise ValueError('生成验证摘要期间输入被修改。')
        while True:
            deadline.check()
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        if current_version() != version:
            raise ValueError('生成验证摘要期间输入被修改。')
    result = digest.digest()
    if reusable:
        with _cache_lock:
            size = (sys.getsizeof(key) + sys.getsizeof(version) + sum(sys.getsizeof(value) for value in version)
                    + sys.getsizeof(result) + 192)
            previous = _cache.pop(key, None)
            if previous is not None:
                _cache_bytes -= previous[2]
            _cache[key] = (version, result, size)
            _cache_bytes += size
            _cache.move_to_end(key)
            while len(_cache) > MAX_CACHED_FILES or _cache_bytes > MAX_CACHED_BYTES:
                _, removed = _cache.popitem(last=False)
                _cache_bytes -= removed[2]
    return result


def _identity_entry(digest, kind, relative, mode, payload=b''):
    # Length framing makes types, names, modes and binary payloads unambiguous.
    for part in (kind, os.fsencode(relative) if relative.parts else b'',
                 str(stat.S_IMODE(mode)).encode(), payload):
        digest.update(len(part).to_bytes(8, 'big'))
        digest.update(part)


@dataclass(frozen=True)
class SnapshotIdentity:
    inputs: str
    stability: str
    artifacts: str
    artifact_count: int


def snapshot(root, generated, git_state, artifact_sink=None, *, extra_exclusions=(), mutable_directories=()):
    root = Path(root).resolve()
    exclusions = generated if getattr(generated, 'disk_membership', False) else {Path(item) for item in generated}
    deadline = _Deadline()
    scan_time = time.time_ns()
    git_before = git_state()
    digest = hashlib.sha256(INPUT_DOMAIN)
    stability = hashlib.sha256(b'ignovate-input-stability-v1\0')
    artifacts = hashlib.sha256(b'ignovate-artifacts-v1\0')
    artifact_count = 0
    extra = {Path(path) for path in extra_exclusions}
    mutable = {Path(path) for path in mutable_directories}
    class SourceLinkExclusions:
        def __contains__(self, path):
            return path in exclusions or path in extra
    source_link_exclusions = SourceLinkExclusions()
    def entry(kind, relative, version, payload=b''):
        nonlocal artifact_count
        if relative in extra:
            return
        output = relative in exclusions
        _identity_entry(artifacts if output else digest, kind, relative, version[2], payload)
        if output:
            artifact_count += 1
            if artifact_sink is not None:
                artifact_sink(relative, kind.decode(), stat.S_IMODE(version[2]), payload.hex())
        else:
            stable_version = version[:3] if kind == b'directory' and relative in mutable else version
            _identity_entry(stability, kind, relative, version[2], repr(stable_version).encode())
    with inventory_database(root, deadline) as inventory:
        _inventory(root, exclusions, deadline, inventory, 'before')
        for relative, version, covered in inventory.directory_entries('before'):
            deadline.check()
            entry(b'directory', relative, version)
        for relative, version in inventory.files('before'):
            deadline.check()
            if stat.S_ISLNK(version[2]):
                from nailong.core.snapshot_links import resolve_link
                try:
                    state, target = resolve_link(root, relative, version[6], inventory, 'before',
                        extra if relative in exclusions else source_link_exclusions, deadline)
                except ValueError as error:
                    raise ValueError(f'无法完整覆盖链接 {str(relative)!r}：{error}') from error
                payload = hashlib.sha256(os.fsencode(version[6]) + b'\0' + state.encode()
                                         + b'\0' + os.fsencode(target)).digest()
                entry(b'link', relative, version, payload)
            else:
                content = _file_digest(root, relative, version, deadline, scan_time)
                entry(b'file', relative, version, content)
        _inventory(root, exclusions, deadline, inventory, 'after')
        if not inventory.unchanged() or git_state() != git_before:
            raise ValueError('生成验证摘要期间输入清单、版本或 Git 状态发生变化。')
    deadline.check()
    for part in git_before:
        digest.update(len(part).to_bytes(8, 'big'))
        digest.update(part)
        stability.update(len(part).to_bytes(8, 'big'))
        stability.update(part)
    return SnapshotIdentity(digest.hexdigest(), stability.hexdigest(), artifacts.hexdigest(), artifact_count)


def fingerprint(root, generated, git_state):
    return snapshot(root, generated, git_state).inputs
