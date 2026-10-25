"""Keep one artifact transaction on one native directory identity."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import secrets
import stat

from nailong.core.safe_files import atomic_write_bytes, open_regular_file, pinned_directory

_active=ContextVar('artifact_directory',default=None)


def _identity(handle):
    if os.name=='nt':
        from nailong.core._win32_files import information
        value=information(handle)
        return value.volume,value.index_high,value.index_low
    value=os.fstat(handle)
    return value.st_dev,value.st_ino


class ArtifactDirectory:
    def __init__(self,path,handle):
        self.path=Path(path).absolute()
        self.handle=handle
        self.identity=_identity(handle)

    def check(self):
        try:
            with pinned_directory(self.path) as current:
                if _identity(current)==self.identity:return
        except (OSError,ValueError):pass
        raise ValueError('产物目录身份已变化；未操作替换目录。')

    @staticmethod
    def name(name):
        if not name or name in {'.','..'} or any(char in name for char in '/\\\0'):
            raise ValueError('产物操作需要同目录普通文件名。')
        return name

    def read(self,name,limit):
        name=self.name(name)
        if os.name=='nt':
            # The native pin retains all ancestors without write/delete sharing.
            with open_regular_file(self.path/name) as source:return source.read(limit+1)
        descriptor=os.open(name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=self.handle)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ValueError('产物存储文件必须是普通文件。')
        with os.fdopen(descriptor,'rb') as source:return source.read(limit+1)

    def write(self,name,data,*,replace=True):
        name=self.name(name)
        if os.name=='nt':
            atomic_write_bytes(self.path/name,data,replace=replace)
            return
        try:value=os.stat(name,dir_fd=self.handle,follow_symlinks=False)
        except FileNotFoundError:pass
        else:
            if not stat.S_ISREG(value.st_mode):raise ValueError('产物发布不能替换链接或目录。')
        temporary='.'+secrets.token_hex(16)+'.tmp'
        descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.handle)
        try:
            with os.fdopen(descriptor,'wb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
            if replace:os.replace(temporary,name,src_dir_fd=self.handle,dst_dir_fd=self.handle)
            else:os.link(temporary,name,src_dir_fd=self.handle,dst_dir_fd=self.handle,follow_symlinks=False)
            os.fsync(self.handle)
        finally:
            try:os.unlink(temporary,dir_fd=self.handle)
            except FileNotFoundError:pass

    def unlink(self,name):
        name=self.name(name)
        value=(self.path/name).lstat() if os.name=='nt' else os.stat(name,dir_fd=self.handle,follow_symlinks=False)
        if not stat.S_ISREG(value.st_mode) or getattr(value,'st_file_attributes',0)&0x400:
            raise ValueError('产物回收不能删除链接、目录或未知条目。')
        if os.name=='nt':(self.path/name).unlink()
        else:
            os.unlink(name,dir_fd=self.handle);os.fsync(self.handle)
        return value.st_size


@contextmanager
def bind_directory(path,*,create=False):
    with pinned_directory(path,create=create) as handle:
        owner=ArtifactDirectory(path,handle);token=_active.set(owner)
        try:
            yield owner
            owner.check()
        finally:_active.reset(token)


def current_directory(path):
    owner=_active.get()
    if owner is None or owner.path!=Path(path).absolute():
        raise ValueError('产物操作不属于当前目录持有者。')
    return owner


def read_bytes(path,limit):
    path=Path(path);owner=_active.get()
    if owner is not None:return current_directory(path.parent).read(path.name,limit)
    with open_regular_file(path) as source:return source.read(limit+1)


def write_bytes(path,data,*,replace=True):
    path=Path(path);owner=_active.get()
    if owner is not None:return current_directory(path.parent).write(path.name,data,replace=replace)
    return atomic_write_bytes(path,data,replace=replace)
