"""Serialized artifact publication and conservative orphan recovery.

Complete headers are roots, including historical headers. A partial reference
graph never authorizes deletion. Callers own their anonymous staging file.
"""
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import time

from nailong.core.file_locks import file_lock
from nailong.core.input_snapshot import _Deadline
from nailong.core.artifact_directory import bind_directory,current_directory,read_bytes,write_bytes

METADATA_BYTES=256*1024
MAX_GRAPH_BYTES=512*1024*1024
_HEX=re.compile(r'[a-f0-9]{64}\Z')
_TEMP=re.compile(r'\.[a-f0-9]{32}\.tmp\Z')
_INTENT='publication.json'
_MAINTENANCE='maintenance.json'


def _directory(boundary):
    return Path(boundary)/'verification-artifacts-v1'


def _read(path, limit=METADATA_BYTES):
    data=read_bytes(path,limit)
    if len(data)>limit:raise ValueError('产物存储记录超过容量。')
    return data


def _write(path, value):
    data=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    if len(data)>METADATA_BYTES:raise ValueError('产物存储记录超过容量。')
    write_bytes(path,data)


@contextmanager
def _locked(boundary, deadline):
    with ExitStack() as stack:
        while True:
            deadline.check()
            try:
                stack.enter_context(file_lock(Path(boundary)/'artifact-publication.lock',blocking=False))
                break
            except BlockingIOError:time.sleep(.01)
        yield


def _result():
    return {'removed_parts':0,'removed_bytes':0,'warnings':[]}


def _merge(destination, result):
    destination['removed_parts']+=result['removed_parts']
    destination['removed_bytes']+=result['removed_bytes']
    destination['warnings']=(destination['warnings']+result['warnings'])[:8]


def _header_parts(data, identity):
    if hashlib.sha256(data).hexdigest()!=identity:raise ValueError('历史产物头校验失败。')
    try:
        header=json.loads(data)
        if (type(header.get('version')) is not int or header['version']!=1
                or not isinstance(header.get('project'),str)
                or type(header.get('count')) is not int or header['count']<0
                or type(header.get('bytes')) is not int or not 0<=header['bytes']<=64*1024*1024
                or not isinstance(header.get('artifacts'),str) or not _HEX.fullmatch(header['artifacts'])
                or not isinstance(header.get('parts'),list) or len(header['parts'])>128):
            raise ValueError('历史产物头结构无效。')
        total=0
        for identity,size in header['parts']:
            if not isinstance(identity,str) or not _HEX.fullmatch(identity) or type(size) is not int or not 0<size<=1024*1024:
                raise ValueError('历史产物分片引用无效。')
            total+=size
        if total!=header['bytes']:raise ValueError('历史产物头不完整。')
        return header['parts']
    except (TypeError,KeyError,AttributeError,UnicodeError,RecursionError,json.JSONDecodeError) as error:
        raise ValueError('历史产物头结构无效。') from error


def _unlink(directory, name):
    # Only flat owned filenames, never recursive paths or link targets.
    return current_directory(directory).unlink(name)


def _collect_locked(boundary, deadline, only=None):
    from nailong.core.verification_artifacts import artifact_index
    directory=_directory(boundary);result=_result();total=0
    # The graph is complete before the first deletion. SQLite is temporary,
    # bounded, and closed before the publication owner releases its lock.
    try:
        with artifact_index() as referenced:
            handle=current_directory(directory).handle
            with os.scandir(directory if os.name=='nt' else handle) as entries:
                for entry in entries:
                    deadline.check();name=entry.name
                    if name in {_INTENT,_MAINTENANCE}:continue
                    if name.endswith('.json'):
                        identity=name[:-5]
                        if not _HEX.fullmatch(identity):raise ValueError('未知历史产物头。')
                        data=_read(directory/name);total+=len(data)
                        if total>MAX_GRAPH_BYTES:raise ValueError('历史产物引用图超过容量。')
                        for identity,_ in _header_parts(data,identity):referenced.add(Path(identity+'.part'))
                    elif name.endswith('.part') or _TEMP.fullmatch(name):
                        if (name.endswith('.part') and not _HEX.fullmatch(name[:-5])
                                or not entry.is_file(follow_symlinks=False)
                                or getattr(entry.stat(follow_symlinks=False),'st_file_attributes',0)&0x400):
                            raise ValueError('未知产物分片条目。')
            with os.scandir(directory if os.name=='nt' else handle) as entries:
                for entry in entries:
                    deadline.check();name=entry.name
                    part=name.endswith('.part') and _HEX.fullmatch(name[:-5])
                    temporary=_TEMP.fullmatch(name)
                    if not (part or temporary) or only is not None and name not in only:continue
                    if part and Path(name) in referenced:continue
                    result['removed_bytes']+=_unlink(directory,name)
                    result['removed_parts']+=bool(part)
    except (ValueError,OSError):
        if deadline.stopped():raise
        result['warnings'].append('完整产物引用图或回收未完成；未依据部分引用图删除，未核实条目保留。')
    return result


def _recover_locked(boundary, deadline):
    directory=_directory(boundary)
    try:data=_read(directory/_INTENT)
    except FileNotFoundError:return _result()
    try:
        intent=json.loads(data)
        if (set(intent)!={'version','header','created'} or type(intent['version']) is not int or intent['version']!=1
                or not isinstance(intent['header'],str) or not _HEX.fullmatch(intent['header'])
                or not isinstance(intent['created'],list) or len(intent['created'])>128
                or any(not isinstance(name,str) or not re.fullmatch(r'[a-f0-9]{64}\.part',name) for name in intent['created'])
                or len(set(intent['created']))!=len(intent['created'])):
            raise ValueError('产物发布意图损坏，未删除或重置。')
    except (TypeError,KeyError,AttributeError,UnicodeError,RecursionError,json.JSONDecodeError) as error:
        raise ValueError('产物发布意图损坏，未删除或重置。') from error
    try:header=_read(directory/(intent['header']+'.json'))
    except FileNotFoundError:
        result=_collect_locked(boundary,deadline,set(intent['created']))
    else:
        parts={identity+'.part' for identity,_ in _header_parts(header,intent['header'])}
        if not set(intent['created'])<=parts:raise ValueError('产物发布意图与完整头不匹配。')
        result=_result()
    if result['warnings']:
        _write(directory/_MAINTENANCE,{'version':1,**result})
    _unlink(directory,_INTENT)
    return result


def collect_orphan_parts(boundary):
    """Keep every complete header; collect only proven unreferenced flat parts."""
    deadline=_Deadline()
    with _locked(boundary,deadline),bind_directory(_directory(boundary),create=True):
        result=_recover_locked(boundary,deadline)
        _merge(result,_collect_locked(boundary,deadline))
        _write(_directory(boundary)/_MAINTENANCE,{'version':1,**result})
        return result


def publish_manifest(boundary, header, spool, parts, publish):
    """Publish only a completed staged observation, with recoverable intent."""
    deadline=_Deadline();directory=_directory(boundary);identity=hashlib.sha256(header).hexdigest()
    if len(header)>METADATA_BYTES:raise ValueError('完整产物头超过容量。')
    expected=_header_parts(header,identity)
    if [(key,size) for _,key,size in parts]!=[tuple(row) for row in expected]:
        raise ValueError('暂存产物清单不完整。')
    with _locked(boundary,deadline),bind_directory(directory,create=True):
        result=_recover_locked(boundary,deadline)
        try:maintenance=json.loads(_read(directory/_MAINTENANCE))
        except FileNotFoundError:
            _merge(result,_collect_locked(boundary,deadline))
            _write(directory/_MAINTENANCE,{'version':1,**result})
        else:
            if not isinstance(maintenance,dict) or maintenance.get('version')!=1:
                raise ValueError('产物存储维护记录损坏。')
            result['warnings']=(result['warnings']+maintenance.get('warnings',[]))[:8]
        created=[]
        for offset,key,size in parts:
            deadline.check();spool.seek(offset);data=spool.read(size)
            if len(data)!=size or hashlib.sha256(data).hexdigest()!=key:
                raise ValueError('暂存产物分片缺失或损坏。')
            try:existing=_read(directory/(key+'.part'),size)
            except FileNotFoundError:created.append(key+'.part')
            else:
                if existing!=data:raise ValueError('不可变产物分片已损坏。')
        _write(directory/_INTENT,{'version':1,'header':identity,'created':sorted(set(created))})
        try:
            for offset,key,size in parts:
                deadline.check();spool.seek(offset);data=spool.read(size)
                if len(data)!=size or hashlib.sha256(data).hexdigest()!=key:
                    raise ValueError('暂存产物分片缺失或损坏。')
                if publish(boundary,data,'.part')!=key:raise ValueError('产物发布身份不一致。')
            deadline.check()
            if publish(boundary,header,'.json')!=identity:raise ValueError('产物发布头身份不一致。')
        except BaseException:
            # If cancellation or storage failure prevents cleanup, the durable
            # intent survives and the next owner performs the same recovery.
            try:_recover_locked(boundary,deadline)
            except (ValueError,OSError):pass
            raise
        try:_unlink(directory,_INTENT)
        except (OSError,ValueError):
            result['warnings']=(result['warnings']+['完整产物头已提交；发布意图清理失败，留待下一持有者恢复。'])[:8]
            try:_write(directory/_MAINTENANCE,{'version':1,**result})
            except (OSError,ValueError):pass
        return identity,result
