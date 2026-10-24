"""Per-thread immutable history publication with bounded crash recovery."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import time

from nailong.core import history_chunks
from nailong.core.artifact_directory import ArtifactDirectory
from nailong.core.file_locks import file_lock, _open_posix_lock
from nailong.core.input_snapshot import _Deadline

METADATA_BYTES=256*1024
MAX_GRAPH_BYTES=512*1024*1024
INTENT='.history-publication-v1'
MAINTENANCE='.history-maintenance-v1'
LOCK='.history-publication.lock'
_HEAD=re.compile(r'hist_[a-f0-9]{64}\.json\Z')
_PART=re.compile(r'histpart_[a-f0-9]{64}\.bin\Z')
_TEMP=re.compile(r'\.[a-f0-9]{32}\.tmp\Z')


def _result():return {'removed_parts':0,'removed_bytes':0,'warnings':[]}


def _merge(destination,source):
    for key in ('removed_parts','removed_bytes'):destination[key]+=source[key]
    destination['warnings']=(destination['warnings']+source['warnings'])[:8]


def _read(owner,name,limit=METADATA_BYTES):
    data=owner.read(name,limit)
    if len(data)>limit:raise ValueError('历史存储元数据超过容量。')
    return data


def _write(owner,name,value):
    data=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    if len(data)>METADATA_BYTES:raise ValueError('历史存储元数据超过容量。')
    owner.write(name,data)


@contextmanager
def _locked(owner,deadline):
    if os.name=='nt':
        while True:
            deadline.check()
            try:
                context=file_lock(owner.path/LOCK,blocking=False);context.__enter__();break
            except BlockingIOError:time.sleep(.01)
        try:yield
        finally:context.__exit__(None,None,None)
        return
    import fcntl
    descriptor=_open_posix_lock(owner.handle,LOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):raise ValueError('历史存储锁必须是普通文件。')
        while True:
            deadline.check()
            try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:time.sleep(.01)
        yield
    finally:os.close(descriptor)


def _head_parts(data,reference,thread_key):
    header=json.loads(data)
    if (not isinstance(header,dict) or header.get('reference')!=reference
            or header.get('thread_key')!=thread_key or type(header.get('schema_version')) is not int):
        raise ValueError('历史引用图身份无效。')
    if header['schema_version']==2:
        return {'histpart_'+row['sha256']+'.bin' for row in history_chunks.manifest_parts(header)}
    if (header['schema_version']!=1 or not isinstance(header.get('message'),dict)
            or not isinstance(header['message'].get('data'),dict) or 'content' not in header['message']['data']
            or 'archive_truncated' in header and type(header['archive_truncated']) is not bool):
        raise ValueError('历史引用图根无效。')
    return set()


def _collect(owner,thread_key,deadline,only=None):
    from nailong.core.history_archive import MAX_ARCHIVE_BYTES
    from nailong.core.verification_artifacts import artifact_index
    result=_result();total=0
    try:
        with artifact_index() as referenced:
            with os.scandir(owner.path if os.name=='nt' else owner.handle) as entries:
                for entry in entries:
                    deadline.check();name=entry.name
                    if _HEAD.fullmatch(name):
                        data=_read(owner,name,MAX_ARCHIVE_BYTES);total+=len(data)
                        if total>MAX_GRAPH_BYTES:raise ValueError('完整历史引用图超过容量。')
                        parts=_head_parts(data,name[:-5],thread_key)
                        header=json.loads(data)
                        if header['schema_version']==2:
                            total+=header['size_bytes']
                            if total>MAX_GRAPH_BYTES:raise ValueError('完整历史引用图核验超过容量。')
                            def read_part(part,limit):
                                deadline.check()
                                return _read(owner,part,limit)
                            record=history_chunks.load(header,read_part)
                        else:record=header
                        from nailong.core.history_archive import HistoryArchive
                        HistoryArchive._validate_record(record,name[:-5],thread_key)
                        for part in parts:referenced.add(Path(part))
                    elif name.endswith('.json') or name.startswith('histpart_') and not _PART.fullmatch(name):
                        raise ValueError('历史存储含未知根或分片名称。')
                    elif _PART.fullmatch(name) or _TEMP.fullmatch(name):
                        value=entry.stat(follow_symlinks=False)
                        if not stat.S_ISREG(value.st_mode) or getattr(value,'st_file_attributes',0)&0x400:
                            raise ValueError('历史回收不能处理链接或非普通条目。')
            # Do not delete before the entire bounded root graph was validated.
            with os.scandir(owner.path if os.name=='nt' else owner.handle) as entries:
                for entry in entries:
                    deadline.check();name=entry.name;part=bool(_PART.fullmatch(name))
                    if not (part or _TEMP.fullmatch(name)) or only is not None and part and name not in only:continue
                    if part and Path(name) in referenced:continue
                    result['removed_bytes']+=owner.unlink(name)
                    result['removed_parts']+=part
    except (OSError,ValueError,TypeError,RecursionError):
        if deadline.stopped():raise
        result['warnings'].append('完整历史引用图或回收未完成；未依据部分引用图删除，未核实分片保留。')
    return result


def _recover(owner,thread_key,deadline,requested=None):
    try:data=_read(owner,INTENT)
    except FileNotFoundError:return _result()
    try:
        intent=json.loads(data)
        if (not isinstance(intent,dict) or set(intent)!={'version','header','created'}
                or type(intent['version']) is not int or intent['version']!=1
                or not isinstance(intent['header'],str) or not _HEAD.fullmatch(intent['header']+'.json')
                or not isinstance(intent['created'],list) or len(intent['created'])>64
                or any(not isinstance(name,str) or not _PART.fullmatch(name) for name in intent['created'])
                or len(set(intent['created']))!=len(intent['created'])):
            raise ValueError('历史发布意图损坏，未删除或重置。')
    except (UnicodeError,TypeError,KeyError,RecursionError,json.JSONDecodeError) as error:
        raise ValueError('历史发布意图损坏，未删除或重置。') from error
    from nailong.core.history_archive import MAX_ARCHIVE_BYTES
    committed=False
    try:header=_read(owner,intent['header']+'.json',MAX_ARCHIVE_BYTES)
    except FileNotFoundError:result=_collect(owner,thread_key,deadline,set(intent['created']))
    else:
        committed=True
        if not set(intent['created'])<=_head_parts(header,intent['header'],thread_key):
            raise ValueError('历史发布意图与完整头不一致。')
        result=_collect(owner,thread_key,deadline,set())
    if result['warnings']:_write(owner,MAINTENANCE,{'version':1,**result})
    try:owner.unlink(INTENT)
    except (OSError,ValueError):
        # Only the exact validated committed record can bypass failed journal
        # retirement. Another publication must not overwrite unresolved state.
        if not committed or requested!=intent['header']:raise
        _warn_cleanup(owner,result)
    return result



def _warn_cleanup(owner,result):
    result['warnings']=(result['warnings']+['完整历史头已提交；发布日志清理失败，留待下一持有者恢复。'])[:8]
    try:_write(owner,MAINTENANCE,{'version':1,**result})
    except (OSError,ValueError):pass


def _maintenance(owner,thread_key,deadline,result):
    try:maintenance=json.loads(_read(owner,MAINTENANCE))
    except FileNotFoundError:
        _merge(result,_collect(owner,thread_key,deadline))
        _write(owner,MAINTENANCE,{'version':1,**result})
    else:
        warnings=maintenance.get('warnings') if isinstance(maintenance,dict) else None
        if (not isinstance(maintenance,dict) or type(maintenance.get('version')) is not int or maintenance['version']!=1
                or not isinstance(warnings,list) or len(warnings)>8
                or any(not isinstance(value,str) or len(value)>1000 for value in warnings)):
            raise ValueError('历史存储维护记录损坏。')
        result['warnings']=(result['warnings']+warnings)[:8]


def _check(owner):
    try:owner.check()
    except ValueError as error:raise ValueError('历史归档目录身份已变化；未操作替换目录。') from error


def publish_record(archive,directory,reference,record):
    from nailong.core.history_archive import _encode,MAX_ARCHIVE_BYTES
    path=archive.root/record['thread_key'];handle=directory.handle if os.name=='nt' else directory
    owner=ArtifactDirectory(path,handle);deadline=_Deadline();thread_key=record['thread_key']
    with _locked(owner,deadline):
        _check(owner);result=_recover(owner,thread_key,deadline,requested=reference)
        _maintenance(owner,thread_key,deadline,result)
        # A pre-existing immutable header must never be replaced or rewritten
        # with different archival metadata merely because its part is missing.
        try:_read(owner,reference+'.json',MAX_ARCHIVE_BYTES)
        except FileNotFoundError:pass
        else:
            archive._load(directory,reference,thread_key);_check(owner)
            return reference,result
        payload=_encode(record)
        def prepare(header):
            created=[]
            for row in history_chunks.manifest_parts(header):
                deadline.check();name='histpart_'+row['sha256']+'.bin'
                try:_read(owner,name,history_chunks.PART_BYTES)
                except FileNotFoundError:created.append(name)
            _write(owner,INTENT,{'version':1,'header':reference,'created':sorted(set(created))})
        try:
            if record['schema_version']==2:
                header=history_chunks.save(payload,reference,thread_key,
                    lambda name,value:archive._publish(directory,name,value),
                    lambda name,limit:archive._read_bytes(directory,name,limit),prepared=prepare)
                payload=_encode(header)
                if len(payload)>MAX_ARCHIVE_BYTES:raise ValueError('历史归档清单超过大小限制。')
            else:_write(owner,INTENT,{'version':1,'header':reference,'created':[]})
            deadline.check()
            try:archive._publish(directory,reference+'.json',payload)
            except FileExistsError:pass
            archive._load(directory,reference,thread_key)
            _check(owner)
        except BaseException:
            try:_merge(result,_recover(owner,thread_key,deadline))
            except (ValueError,OSError):pass
            raise
        try:owner.unlink(INTENT)
        except (OSError,ValueError):_warn_cleanup(owner,result)
        return reference,result


def collect_orphan_parts(archive,directory,thread_key):
    handle=directory.handle if os.name=='nt' else directory
    owner=ArtifactDirectory(archive.root/thread_key,handle);deadline=_Deadline()
    with _locked(owner,deadline):
        _check(owner);result=_recover(owner,thread_key,deadline)
        _merge(result,_collect(owner,thread_key,deadline))
        _write(owner,MAINTENANCE,{'version':1,**result});_check(owner)
        return result
