"""Run on native Windows; isolate directory sharing from publication APIs."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
ntdll = ctypes.WinDLL('ntdll', use_last_error=True)
create = kernel.CreateFileW
create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
create.restype = wintypes.HANDLE
close = kernel.CloseHandle
close.argtypes = [wintypes.HANDLE]
class Rename(ctypes.Structure):
    _fields_ = [('replace', wintypes.BYTE), ('root', wintypes.HANDLE),
                ('length', wintypes.DWORD), ('name', wintypes.WCHAR * 1)]
class IOStatus(ctypes.Structure):
    _fields_ = [('status', wintypes.LPVOID), ('information', ctypes.c_size_t)]
set_info = ntdll.NtSetInformationFile
set_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(IOStatus), wintypes.LPVOID,
                     wintypes.DWORD, wintypes.DWORD]
set_info.restype = ctypes.c_long
invalid = ctypes.c_void_p(-1).value

def open_handle(path, access, share):
    handle = create('\\\\?\\' + str(path), access, share, None, 3, 0x2200000, None)
    if handle == invalid:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary).resolve()
    for access in (0x80, 0x81, 0xa0):
        for share in (1, 3):
            for kind in ('pathname', 'relative-root', 'relative-same-parent'):
                parent = root / f'{access}-{share}-{kind}'
                parent.mkdir()
                source, target = parent / 'source', parent / 'target'
                source.write_bytes(b'complete')
                pin = open_handle(parent, access, share)
                output = dict(access=hex(access), share=share, kind=kind)
                try:
                    attack = create('\\\\?\\' + str(parent), 0x10000, 7, None, 3, 0x2200000, None)
                    output['delete_open'] = 'success' if attack != invalid else ctypes.get_last_error()
                    if attack != invalid: close(attack)
                    try:
                        if kind == 'pathname':
                            os.replace(source, target)
                            output['rename'] = 'success'
                        else:
                            source_handle = open_handle(source, 0x10000 | 0x80, 7)
                            try:
                                name = target.name.encode('utf-16-le')
                                buffer = ctypes.create_string_buffer(ctypes.sizeof(Rename) + len(name))
                                information = Rename.from_buffer(buffer)
                                information.replace = 1
                                information.root = pin if kind == 'relative-root' else None
                                information.length = len(name)
                                ctypes.memmove(ctypes.addressof(buffer) + Rename.name.offset, name, len(name))
                                status = set_info(source_handle, ctypes.byref(IOStatus()), buffer, len(buffer), 10)
                                output['rename'] = 'success' if status >= 0 else hex(status & 0xffffffff)
                            finally: close(source_handle)
                    except OSError as error:
                        output['rename'] = error.winerror
                    output['target_data'] = target.read_text() if target.exists() else None
                finally: close(pin)
                print(json.dumps(output), flush=True)
