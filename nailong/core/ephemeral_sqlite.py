"""Bounded private disk scratch databases, owned by SQLite and OS handles."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sqlite3
import tempfile


def _windows_temp_directory():
    # Match the default SQLite Windows VFS; never mutate process temp settings.
    function=ctypes.WinDLL('kernel32',use_last_error=True).GetTempPathW
    function.argtypes=[wintypes.DWORD,wintypes.LPWSTR];function.restype=wintypes.DWORD
    buffer=ctypes.create_unicode_buffer(32768)
    length=function(len(buffer),buffer)
    if not length:raise ctypes.WinError(ctypes.get_last_error())
    if length>=len(buffer):raise ValueError('系统临时目录超过可核对长度。')
    return Path(buffer.value)


def _sqlite_temp_directory():
    if os.name=='nt':
        directory=_windows_temp_directory()
        if directory.is_dir() and os.access(directory,os.W_OK|os.X_OK):return directory.resolve()
    else:
        for raw in (os.environ.get('SQLITE_TMPDIR'),os.environ.get('TMPDIR'),'/var/tmp','/usr/tmp','/tmp','.'):
            if not raw:continue
            directory=Path(raw)
            if directory.is_dir() and os.access(directory,os.W_OK|os.X_OK):return directory.resolve()
    raise ValueError('无法核对SQLite临时磁盘目录。')


@contextmanager
def scratch_database(deadline,*,max_pages,excluded_root=None):
    if type(max_pages) is not int or not 1<=max_pages<=131072:
        raise ValueError('临时磁盘索引页数预算无效。')
    directory=_sqlite_temp_directory()
    if excluded_root is not None:
        root=Path(excluded_root).resolve()
        if any(path.is_relative_to(root) for path in (directory,Path(tempfile.gettempdir()).resolve())):
            raise ValueError('验证摘要的临时索引不能位于项目输入内。')
    connection=None
    try:
        deadline.check();connection=sqlite3.connect('')
        options={row[0] for row in connection.execute('PRAGMA compile_options')}
        if not options.intersection({'TEMP_STORE=0','TEMP_STORE=1','TEMP_STORE=2'}):
            raise ValueError('当前SQLite构建无法保证临时磁盘索引；未回退到内存。')
        # The data-bearing database is opened only AFTER FILE was selected.
        # Main remains empty even when a build defaults temporary DBs to memory.
        connection.execute('PRAGMA temp_store=FILE')
        connection.execute('PRAGMA main.max_page_count=1')
        connection.execute("ATTACH DATABASE '' AS scratch")
        connection.execute('PRAGMA scratch.page_size=4096')
        connection.execute(f'PRAGMA scratch.max_page_count={max_pages}')
        connection.execute('PRAGMA scratch.cache_size=-2048')
        connection.execute('PRAGMA scratch.journal_mode=OFF')
        connection.execute('PRAGMA scratch.synchronous=OFF')
        connection.set_progress_handler(lambda:int(deadline.stopped()),1000)
        deadline.check()
        yield connection
    finally:
        if connection is not None:connection.close()
