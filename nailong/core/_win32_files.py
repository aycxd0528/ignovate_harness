"""Win32 handle primitives. Imported only by the Windows backend."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from contextlib import contextmanager, nullcontext
from functools import lru_cache
import os

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
security = ctypes.WinDLL('advapi32', use_last_error=True)
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
READ_ATTRIBUTES, WRITE_DAC = 0x80, 0x40000
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
ReplaceFile = _bind(kernel, 'ReplaceFileW', [wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID], wintypes.BOOL)
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
        handles.append(open_handle(current, directory=True, access=READ_ATTRIBUTES))
        for part in path.parts[1:]:
            current /= part
            try:
                handle = open_handle(current, directory=True, access=READ_ATTRIBUTES)
            except FileNotFoundError:
                if not create:
                    raise
                with private_security(directory=True) if private else nullcontext() as attributes:
                    if not CreateDirectory(extended(current), ctypes.byref(attributes) if attributes else None):
                        if ctypes.get_last_error() != 183:
                            raise _error(current)
                handle = open_handle(current, directory=True, access=READ_ATTRIBUTES)
            handles.append(handle)
        yield handles[-1]
    finally:
        for handle in reversed(handles):
            CloseHandle(handle)


def replace_project_file(temporary, destination):
    """Native metadata-preserving replacement; never ignore an ACL merge error."""
    if not ReplaceFile(extended(destination), extended(temporary), None, 0, None, None):
        raise _error(destination)


def set_private_permissions(path, *, directory=False):
    with pin_directory(path.parent):
        handle = open_handle(path, directory=directory, access=READ_ATTRIBUTES | WRITE_DAC)
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
