"""Watch mutable input directory identities through a verification command.

Native rename/delete/attribute notifications distinguish output-entry changes from a
directory being moved away and restored. Without such a native witness the
caller retains strict directory change counters instead of masking changes.
"""
from contextlib import ExitStack
import os
from pathlib import Path
import select
import stat
import sys

from nailong.core.safe_files import pinned_directory


class DirectoryGuard:
    def __init__(self):
        self.stack = ExitStack()
        self.protected = set()
        self.handles = []
        self.queue = None
        self.fd = None

    def start(self, root, paths):
        if sys.platform != 'darwin' and not sys.platform.startswith('linux'):
            return
        try:
            if sys.platform == 'darwin':
                self.queue = select.kqueue()
                self.stack.callback(self.queue.close)
            else:
                import ctypes
                self.libc = ctypes.CDLL(None, use_errno=True)
                self.libc.inotify_init1.argtypes = [ctypes.c_int]
                self.libc.inotify_init1.restype = ctypes.c_int
                self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
                self.libc.inotify_add_watch.restype = ctypes.c_int
                self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
                if self.fd < 0:
                    raise OSError(ctypes.get_errno(), 'inotify_init1')
                self.stack.callback(os.close, self.fd)
            for path in sorted(set(paths)):
                if len(self.handles) >= 4096:
                    raise ValueError('验证目录身份观察超过 4096 个可变父目录。')
                try:
                    handle = self.stack.enter_context(pinned_directory(Path(root) / path))
                except FileNotFoundError:
                    continue
                if self.queue is not None:
                    event = select.kevent(handle, filter=select.KQ_FILTER_VNODE,
                        flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                        fflags=select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE | select.KQ_NOTE_REVOKE | select.KQ_NOTE_ATTRIB)
                    self.queue.control([event], 0, 0)
                else:
                    import ctypes
                    if self.libc.inotify_add_watch(self.fd, os.fsencode(f'/proc/self/fd/{handle}'), 0x800 | 0x400 | 0x2000 | 0x4) < 0:
                        raise OSError(ctypes.get_errno(), 'inotify_add_watch')
                self.handles.append(handle)
                self.protected.add(path)
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        if self.queue is not None:
            if self.queue.control(None, 1, 0):
                raise ValueError('验证期间输入目录被移动、删除、改变属性或撤销，证据失效。')
        elif self.fd is not None:
            try:
                if os.read(self.fd, 65536):
                    raise ValueError('验证期间输入目录身份事件发生变化，证据失效。')
            except BlockingIOError:
                pass
        for handle in self.handles:
            value = os.fstat(handle)
            if not stat.S_ISDIR(value.st_mode) or value.st_nlink == 0:
                raise ValueError('验证期间输入目录身份失效。')

    def close(self):
        self.stack.close()
        self.queue = None
        self.fd = None
        self.handles.clear()
        self.protected.clear()
