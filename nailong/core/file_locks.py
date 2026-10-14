"""Cross-process file locks; Windows uses native synchronous LockFileEx."""
from contextlib import contextmanager
import ctypes
import os
import stat

from nailong.core.safe_files import _path, pinned_directory


@contextmanager
def file_lock(path, *, blocking=True):
    """Hold an exclusive lock; a busy nonblocking attempt raises BlockingIOError."""
    path = _path(path)
    with pinned_directory(path.parent, create=True) as directory:
        if os.name == 'nt':
            from nailong.core._win32_files import (open_handle, private_security, GENERIC_READ,
                GENERIC_WRITE, OPEN_ALWAYS, Overlapped, LockFileEx, UnlockFileEx, CloseHandle)
            with private_security() as attributes:
                handle = open_handle(path, access=GENERIC_READ | GENERIC_WRITE,
                                     creation=OPEN_ALWAYS, attributes=attributes)
            overlapped = Overlapped()
            acquired = False
            try:
                if not LockFileEx(handle, 2 | (0 if blocking else 1), 0, 1, 0, ctypes.byref(overlapped)):
                    code = ctypes.get_last_error()
                    if not blocking and code in {33, 158}:
                        raise BlockingIOError('文件锁已被其他运行持有。')
                    raise ctypes.WinError(code)
                acquired = True
                yield handle
            finally:
                if acquired:
                    UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped))
                CloseHandle(handle)
        else:
            import fcntl
            descriptor = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError('文件锁必须使用普通文件。')
                fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                yield descriptor
            finally:
                os.close(descriptor)
