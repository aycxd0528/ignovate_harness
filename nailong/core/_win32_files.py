"""Win32 handle primitives. Imported only by the Windows backend."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from contextlib import contextmanager, nullcontext
from functools import lru_cache
import os

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
security = ctypes.WinDLL('advapi32', use_last_error=True)
native = ctypes.WinDLL('ntdll', use_last_error=True)
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
LIST_DIRECTORY, READ_ATTRIBUTES, READ_CONTROL, WRITE_DAC = 0x1, 0x80, 0x20000, 0x40000
DELETE = 0x10000
SHARE_READ, SHARE_WRITE = 1, 2
OPEN_EXISTING, OPEN_ALWAYS, CREATE_NEW = 3, 4, 1
REPARSE_POINT, DIRECTORY = 0x400, 0x10
OPEN_REPARSE_POINT, BACKUP_SEMANTICS = 0x00200000, 0x02000000


class FileInformation(ctypes.Structure):
    _fields_ = [('attributes', wintypes.DWORD), ('created', wintypes.FILETIME),
                ('accessed', wintypes.FILETIME), ('written', wintypes.FILETIME),
                ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]


class SecurityAttributes(ctypes.Structure):
    _fields_ = [('length', wintypes.DWORD), ('descriptor', wintypes.LPVOID),
                ('inherit', wintypes.BOOL)]


class Overlapped(ctypes.Structure):
    _fields_ = [('internal', ctypes.c_size_t), ('internal_high', ctypes.c_size_t),
                ('offset', wintypes.DWORD), ('offset_high', wintypes.DWORD),
                ('event', wintypes.HANDLE)]


class IOStatus(ctypes.Structure):
    _fields_ = [('status', wintypes.LPVOID), ('information', ctypes.c_size_t)]


class RenameInformation(ctypes.Structure):
    _fields_ = [('replace', wintypes.BYTE), ('root', wintypes.HANDLE),
                ('length', wintypes.DWORD), ('name', wintypes.WCHAR * 1)]


class BasicInformation(ctypes.Structure):
    _fields_ = [('created', ctypes.c_longlong), ('accessed', ctypes.c_longlong),
                ('written', ctypes.c_longlong), ('changed', ctypes.c_longlong),
                ('attributes', wintypes.DWORD)]


def _bind(library, name, args, result):
    function = getattr(library, name)
    function.argtypes, function.restype = args, result
    return function


CreateFile = _bind(kernel, 'CreateFileW', [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
    ctypes.POINTER(SecurityAttributes), wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE], wintypes.HANDLE)
CloseHandle = _bind(kernel, 'CloseHandle', [wintypes.HANDLE], wintypes.BOOL)
GetInformation = _bind(kernel, 'GetFileInformationByHandle', [wintypes.HANDLE, ctypes.POINTER(FileInformation)], wintypes.BOOL)
GetFileType = _bind(kernel, 'GetFileType', [wintypes.HANDLE], wintypes.DWORD)
CreateDirectory = _bind(kernel, 'CreateDirectoryW', [wintypes.LPCWSTR, ctypes.POINTER(SecurityAttributes)], wintypes.BOOL)
SetInformation = _bind(kernel, 'SetFileInformationByHandle', [wintypes.HANDLE,
    ctypes.c_int, wintypes.LPVOID, wintypes.DWORD], wintypes.BOOL)
GetInformationEx = _bind(kernel, 'GetFileInformationByHandleEx', [wintypes.HANDLE,
    ctypes.c_int, wintypes.LPVOID, wintypes.DWORD], wintypes.BOOL)
NtSetInformation = _bind(native, 'NtSetInformationFile', [wintypes.HANDLE,
    ctypes.POINTER(IOStatus), wintypes.LPVOID, wintypes.DWORD, ctypes.c_int], ctypes.c_long)
NtStatusToDosError = _bind(native, 'RtlNtStatusToDosError', [wintypes.DWORD], wintypes.DWORD)
LockFileEx = _bind(kernel, 'LockFileEx', [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)], wintypes.BOOL)
UnlockFileEx = _bind(kernel, 'UnlockFileEx', [wintypes.HANDLE, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)], wintypes.BOOL)
GetCurrentProcess = _bind(kernel, 'GetCurrentProcess', [], wintypes.HANDLE)
LocalFree = _bind(kernel, 'LocalFree', [wintypes.LPVOID], wintypes.LPVOID)
OpenProcessToken = _bind(security, 'OpenProcessToken', [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)], wintypes.BOOL)
GetTokenInformation = _bind(security, 'GetTokenInformation', [wintypes.HANDLE, ctypes.c_int,
    wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
SidToString = _bind(security, 'ConvertSidToStringSidW', [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)], wintypes.BOOL)
StringToDescriptor = _bind(security, 'ConvertStringSecurityDescriptorToSecurityDescriptorW',
    [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
GetDacl = _bind(security, 'GetSecurityDescriptorDacl', [wintypes.LPVOID,
    ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL)
SetSecurityInfo = _bind(security, 'SetSecurityInfo', [wintypes.HANDLE, ctypes.c_int,
    wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID], wintypes.DWORD)
GetSecurityInfo = _bind(security, 'GetSecurityInfo', [wintypes.HANDLE, ctypes.c_int,
    wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID, ctypes.POINTER(wintypes.LPVOID),
    wintypes.LPVOID, ctypes.POINTER(wintypes.LPVOID)], wintypes.DWORD)
GetDescriptorControl = _bind(security, 'GetSecurityDescriptorControl', [wintypes.LPVOID,
    ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)


def _error(path=None):
    code = ctypes.get_last_error()
    error = ctypes.WinError(code)
    if path is not None:
        error.filename = os.fspath(path)
    return error


def extended(path):
    # Public callers validate the ordinary drive path before adding this prefix.
    return '\\\\?\\' + os.fspath(path)


@lru_cache(maxsize=1)
def _user_sid():
    token = wintypes.HANDLE()
    if not OpenProcessToken(GetCurrentProcess(), 8, ctypes.byref(token)):
        raise _error()
    try:
        needed = wintypes.DWORD()
        GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
            raise _error()
        sid = ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID))[0]
        result = wintypes.LPWSTR()
        if not SidToString(sid, ctypes.byref(result)):
            raise _error()
        try:
            return result.value
        finally:
            LocalFree(ctypes.cast(result, wintypes.LPVOID))
    finally:
        CloseHandle(token)


@contextmanager
def private_security(*, directory=False):
    """Protected DACL for the current user, SYSTEM and administrators only."""
    inheritance = 'OICI' if directory else ''
    descriptor = wintypes.LPVOID()
    sddl = 'D:P' + ''.join(f'(A;{inheritance};FA;;;{sid})' for sid in (_user_sid(), 'SY', 'BA'))
    if not StringToDescriptor(sddl, 1, ctypes.byref(descriptor), None):
        raise _error()
    try:
        yield SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
    finally:
        LocalFree(descriptor)


def information(handle):
    result = FileInformation()
    if not GetInformation(handle, ctypes.byref(result)):
        raise _error()
    return result


def open_handle(path, *, directory=False, access=GENERIC_READ, creation=OPEN_EXISTING, attributes=None):
    flags = OPEN_REPARSE_POINT | BACKUP_SEMANTICS
    # A directory write handle can install a reparse point in place. Deny that
    # as well as DELETE while allowing ordinary child-file creation and writes.
    share = SHARE_READ if directory else SHARE_READ | SHARE_WRITE
    handle = CreateFile(extended(path), access, share,
                        ctypes.byref(attributes) if attributes is not None else None,
                        creation, flags, None)
    if handle == INVALID_HANDLE_VALUE:
        raise _error(path)
    try:
        data = information(handle)
        if data.attributes & REPARSE_POINT:
            raise ValueError('安全文件路径不能经过重解析点、符号链接或目录联接。')
        if bool(data.attributes & DIRECTORY) != directory or GetFileType(handle) != 1:
            raise ValueError('安全路径必须为目录。' if directory else '文件必须是普通文件。')
        return handle
    except BaseException:
        CloseHandle(handle)
        raise


@contextmanager
def pin_directory(path, *, create=False, private=True):
    """Hold every ancestor without FILE_SHARE_DELETE until the operation ends."""
    handles = []
    try:
        current = type(path)(path.anchor)
        # Metadata-only opens do not participate in Windows share accounting.
        # LIST_DIRECTORY gives this handle read access, so its omitted write and
        # delete sharing actually prevents ancestor replacement/reparse updates.
        access = READ_ATTRIBUTES | LIST_DIRECTORY
        handles.append(open_handle(current, directory=True, access=access))
        for part in path.parts[1:]:
            current /= part
            try:
                handle = open_handle(current, directory=True, access=access)
            except FileNotFoundError:
                if not create:
                    raise
                with private_security(directory=True) if private else nullcontext() as attributes:
                    if not CreateDirectory(extended(current), ctypes.byref(attributes) if attributes else None):
                        if ctypes.get_last_error() != 183:
                            raise _error(current)
                handle = open_handle(current, directory=True, access=access)
            handles.append(handle)
        yield handles[-1]
    finally:
        for handle in reversed(handles):
            CloseHandle(handle)


def publish_same_directory(handle, name, *, replace=True):
    """Rename an open DELETE-capable file without reopening its pinned parent.

    A NULL RootDirectory and a single basename identify the source handle's
    existing directory. Passing a full path or RootDirectory instead causes
    IopOpenLinkOrRenameTarget to reopen the directory for writes, conflicting
    with the pin that prevents in-place reparse-point changes.
    """
    if (not name or name in {'.', '..'} or any(char in name for char in '\\/:\0')):
        raise ValueError('原子文件发布需要同目录的普通文件名。')
    encoded = name.encode('utf-16-le')
    buffer = ctypes.create_string_buffer(ctypes.sizeof(RenameInformation) + len(encoded))
    data = RenameInformation.from_buffer(buffer)
    data.replace, data.root, data.length = bool(replace), None, len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + RenameInformation.name.offset, encoded, len(encoded))
    status = NtSetInformation(handle, ctypes.byref(IOStatus()), buffer, len(buffer), 10)
    if status < 0:
        raise ctypes.WinError(NtStatusToDosError(status & 0xffffffff))


def discard_on_close(handle):
    """Delete a failed temporary through its already granted DELETE handle."""
    delete = wintypes.BYTE(1)
    status = NtSetInformation(handle, ctypes.byref(IOStatus()), ctypes.byref(delete), 1, 13)
    if status < 0:
        raise ctypes.WinError(NtStatusToDosError(status & 0xffffffff))


def _check_basic_metadata(handle, data):
    # These need dedicated encryption/compression/sparse and stream-copy APIs.
    # Refuse edits rather than quietly stripping metadata from an existing file.
    if data.attributes & (0x200 | 0x800 | 0x4000):
        raise ValueError('项目文件包含稀疏、压缩或加密属性，不能安全替换。')
    size = 4096
    while True:
        buffer = ctypes.create_string_buffer(size)
        if GetInformationEx(handle, 7, buffer, size):  # FileStreamInfo
            offset = 0
            while True:
                next_offset, length = (wintypes.DWORD.from_buffer(buffer, offset).value,
                                       wintypes.DWORD.from_buffer(buffer, offset + 4).value)
                if length > size - offset - 24:
                    raise ValueError('项目文件数据流信息无效。')
                name = bytes(buffer[offset + 24:offset + 24 + length]).decode('utf-16-le')
                if name != '::$DATA':
                    raise ValueError('项目文件包含附加数据流，不能安全替换。')
                if not next_offset:
                    return
                if next_offset < 24 or offset + next_offset >= size:
                    raise ValueError('项目文件数据流信息无效。')
                offset += next_offset
        elif ctypes.get_last_error() in {38}:  # ERROR_HANDLE_EOF: no streams
            return
        elif ctypes.get_last_error() in {122, 234} and size < 1024 * 1024:
            size *= 2
        else:
            raise _error()


def copy_project_metadata(temporary_handle, destination):
    """Copy a verified target's DACL/protection and basic attributes, fail closed."""
    handle = open_handle(destination, access=GENERIC_READ | GENERIC_WRITE)
    descriptor, dacl = wintypes.LPVOID(), wintypes.LPVOID()
    try:
        data = information(handle)
        if data.attributes & 1:
            raise PermissionError('项目文件为只读文件，保留原文件和权限。')
        _check_basic_metadata(handle, data)
        code = GetSecurityInfo(handle, 1, 4, None, None, ctypes.byref(dacl), None,
                               ctypes.byref(descriptor))
        if code:
            raise ctypes.WinError(code)
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not GetDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise _error(destination)
        protection = 0x80000000 if control.value & 0x1000 else 0x20000000
        code = SetSecurityInfo(temporary_handle, 1, 4 | protection, None, None, dacl, None)
        if code:
            raise ctypes.WinError(code)
        basic = BasicInformation(
            (data.created.dwHighDateTime << 32) | data.created.dwLowDateTime,
            (data.accessed.dwHighDateTime << 32) | data.accessed.dwLowDateTime,
            0, 0, data.attributes)
        if not SetInformation(temporary_handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)):
            raise _error(destination)
    finally:
        if descriptor:
            LocalFree(descriptor)
        CloseHandle(handle)


def set_private_permissions(path, *, directory=False):
    with pin_directory(path.parent):
        # SetSecurityInfo also queries the existing descriptor while handling
        # inheritance/protection. WRITE_DAC alone cannot grant that query.
        # Include data/list read access so the leaf also participates in share
        # accounting and cannot be replaced while its descriptor is changed.
        access = READ_ATTRIBUTES | READ_CONTROL | WRITE_DAC | (LIST_DIRECTORY if directory else GENERIC_READ)
        handle = open_handle(path, directory=directory, access=access)
        try:
            with private_security(directory=directory) as attributes:
                present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), wintypes.LPVOID()
                if not GetDacl(attributes.descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)):
                    raise _error()
                code = SetSecurityInfo(handle, 1, 4 | 0x80000000, None, None, dacl, None)
                if code:
                    raise ctypes.WinError(code)
        finally:
            CloseHandle(handle)
