"""Safe regular-file access and atomic storage on POSIX and native Windows."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import os
from pathlib import Path, PureWindowsPath
import re
import secrets
import stat

_DEVICE = re.compile(r'^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)', re.I)


def validate_windows_path(path):
    """Reject NT/device namespaces, ADS and Win32 path normalization aliases."""
    raw = os.fspath(path)
    parsed = PureWindowsPath(raw)
    if ('\0' in raw or raw.startswith(('\\\\', '//')) or not parsed.is_absolute()
            or not re.fullmatch(r'[A-Za-z]:', parsed.drive)):
        raise ValueError('Windows 安全文件访问需要普通的本地盘符绝对路径。')
    for part in parsed.parts[1:]:
        if (part in {'.', '..'} or part.endswith((' ', '.')) or ':' in part
                or any(ord(char) < 32 or char in '<>"|?*' for char in part)
                or _DEVICE.match(part)):
            raise ValueError('Windows 文件路径包含设备名、数据流或不明确的名称。')
    # PurePath discards single-dot components; reject them before normalization.
    if any(part == '.' for part in raw.replace('/', '\\').split('\\')[1:]):
        raise ValueError('Windows 文件路径不能包含相对跳转。')


def _path(path):
    raw = os.fspath(path)
    if os.name == 'nt':
        # Validate before absolute() can normalize an unsafe relative drive path.
        if PureWindowsPath(raw).drive and not PureWindowsPath(raw).is_absolute():
            raise ValueError('Windows 文件路径不能是盘符相对路径。')
        validate_windows_path(raw if PureWindowsPath(raw).is_absolute() else os.path.abspath(raw))
    result = Path(path).expanduser().absolute()
    if '..' in result.parts:
        raise ValueError('安全文件路径不能包含相对跳转。')
    return result


def is_link_or_reparse(path):
    try:
        data = Path(path).lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(data.st_mode) or bool(getattr(data, 'st_file_attributes', 0) & 0x400)


@contextmanager
def _posix_directory(path, *, create=False, private=True):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700 if private else 0o777, dir_fd=descriptor)
                except FileExistsError:
                    pass
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def pinned_directory(path, *, create=False, private=True):
    """Yield a POSIX descriptor or Windows handle while the directory is pinned."""
    path = _path(path)
    if os.name == 'nt':
        from nailong.core._win32_files import pin_directory
        with pin_directory(path, create=create, private=private) as handle:
            yield handle
    else:
        with _posix_directory(path, create=create, private=private) as descriptor:
            yield descriptor


@contextmanager
def open_regular_file(path, *, binary=True):
    """Yield a read-only stream; reject link ancestors, reparse points and devices."""
    path = _path(path)
    with pinned_directory(path.parent) as directory:
        if os.name == 'nt':
            import msvcrt
            from nailong.core._win32_files import open_handle, CloseHandle
            handle = open_handle(path)
            try:
                descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                CloseHandle(handle)
                raise
        else:
            # The dir_fd walk is retained even if os.open is wrapped by a caller.
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                raise ValueError('文件必须是普通文件。')
        with os.fdopen(descriptor, 'rb' if binary else 'r', **({} if binary else {'encoding': 'utf-8'})) as stream:
            yield stream


def private_file_permissions(path):
    if os.name == 'nt':
        from nailong.core._win32_files import set_private_permissions
        set_private_permissions(_path(path))
    else:
        os.chmod(path, 0o600, follow_symlinks=False)


def private_directory_permissions(path):
    if os.name == 'nt':
        from nailong.core._win32_files import set_private_permissions
        set_private_permissions(_path(path), directory=True)
    else:
        os.chmod(path, 0o700, follow_symlinks=False)


def atomic_write_bytes(path, data, *, replace=True, private=True):
    """Publish flushed bytes under pinned parents; project edits preserve ACLs."""
    path = _path(path)
    temporary = '.' + secrets.token_hex(16) + '.tmp'
    with pinned_directory(path.parent, create=True, private=private) as directory:
        if is_link_or_reparse(path):
            raise ValueError('安全文件写入不能替换符号链接或重解析点。')
        if os.name == 'nt':
            import msvcrt
            from nailong.core._win32_files import (open_handle, private_security, GENERIC_WRITE,
                                                  CREATE_NEW, CloseHandle)
            target = path.parent / temporary
            with private_security() if private else nullcontext() as attributes:
                handle = open_handle(target, access=GENERIC_WRITE, creation=CREATE_NEW, attributes=attributes)
            try:
                descriptor = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
            except BaseException:
                CloseHandle(handle)
                raise
        else:
            existing_mode = None
            if not private:
                try:
                    existing_mode = stat.S_IMODE(os.stat(path.name, dir_fd=directory, follow_symlinks=False).st_mode)
                except FileNotFoundError:
                    pass
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600 if private else 0o666, dir_fd=directory)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                if os.name != 'nt' and existing_mode is not None:
                    os.fchmod(stream.fileno(), existing_mode)
            if os.name == 'nt':
                if replace:
                    if not private and path.exists():
                        if path.stat(follow_symlinks=False).st_file_attributes & 1:
                            raise PermissionError('项目文件为只读文件，保留原文件和权限。')
                        from nailong.core._win32_files import replace_project_file
                        replace_project_file(target, path)
                    elif private:
                        os.replace(target, path)
                    else:
                        # A new project file inherits its parent's ACL. Do not
                        # overwrite a file that appeared after the existence check.
                        os.rename(target, path)
                else:
                    # Windows rename fails if destination exists; no overwrite race.
                    os.rename(target, path)
            elif replace:
                os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
            else:
                os.link(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            if os.name != 'nt':
                os.fsync(directory)
        finally:
            if os.name == 'nt':
                target.unlink(missing_ok=True)
            else:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
