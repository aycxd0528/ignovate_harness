"""Bounded complete history objects, published through immutable chunk manifests."""
from __future__ import annotations
import hashlib
import json
import re

PART_BYTES=1024*1024
MAX_RECORD_BYTES=64*1024*1024
V2_DOMAIN=b'nailong-history-v2\0'
_DIGEST=re.compile(r'^[a-f0-9]{64}$')


def check_cancelled():
    from nailong.core.input_snapshot import active_snapshot_cancellation
    cancellation=active_snapshot_cancellation.get()
    if cancellation is not None and cancellation.is_set():
        raise ValueError('历史归档已取消，未取得完整保存证明。')


def ensure_size(value):
    """Count canonical JSON without first allocating an oversized encoded string."""
    total=0
    def add(count):
        nonlocal total
        check_cancelled();total+=count
        if total>MAX_RECORD_BYTES:raise ValueError('完整历史记录超过 64 MiB，未截断或替换原始消息。')
    def visit(item):
        if isinstance(item,str):
            add(2)
            for offset in range(0,len(item),PART_BYTES):
                add(len(json.dumps(item[offset:offset+PART_BYTES],ensure_ascii=False).encode())-2)
        elif isinstance(item,dict):
            add(2+max(0,len(item)-1)+len(item))
            for key,value in item.items():visit(key);visit(value)
        elif isinstance(item,list):
            add(2+max(0,len(item)-1))
            for value in item:visit(value)
        else:add(len(json.dumps(item,allow_nan=False).encode()))
    visit(value)
    return total


def save(payload,reference,thread_key,publish,read,*,prepared=None):
    if not 0<len(payload)<=MAX_RECORD_BYTES:raise ValueError('完整历史记录大小无效。')
    parts=[]
    for offset in range(0,len(payload),PART_BYTES):
        check_cancelled();part=payload[offset:offset+PART_BYTES]
        parts.append({'sha256':hashlib.sha256(part).hexdigest(),'size':len(part)})
    header={'schema_version':2,'reference':reference,'thread_key':thread_key,
        'size_bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),'parts':parts}
    if prepared is not None:prepared(header)
    for index,row in enumerate(parts):
        check_cancelled();part=payload[index*PART_BYTES:(index+1)*PART_BYTES]
        name='histpart_'+row['sha256']+'.bin'
        try:existing=read(name,PART_BYTES)
        except FileNotFoundError:
            try:publish(name,part)
            except FileExistsError:pass
            existing=read(name,PART_BYTES)
        if existing!=part:raise ValueError('不可变历史片段已损坏，未覆盖原记录。')
    check_cancelled()
    return header


def manifest_parts(header):
    """Validate the complete bounded root before reading or collecting parts."""
    expected={'schema_version','reference','thread_key','size_bytes','sha256','parts'}
    size=header.get('size_bytes');parts=header.get('parts');digest=header.get('sha256')
    if (set(header)!=expected or type(header.get('schema_version')) is not int or header['schema_version']!=2
            or type(size) is not int or not 0<size<=MAX_RECORD_BYTES
            or not isinstance(digest,str) or not _DIGEST.fullmatch(digest)
            or not isinstance(parts,list) or len(parts)!=(size+PART_BYTES-1)//PART_BYTES):
        raise ValueError('历史归档清单大小或身份无效。')
    for index,row in enumerate(parts):
        if (not isinstance(row,dict) or set(row)!={'sha256','size'}
                or not isinstance(row.get('sha256'),str) or not _DIGEST.fullmatch(row['sha256'])
                or type(row.get('size')) is not int or row['size']!=min(PART_BYTES,size-index*PART_BYTES)):
            raise ValueError('历史归档片段清单无效。')
    return parts


def load(header,read):
    parts=manifest_parts(header);digest=header['sha256']
    payload=bytearray();hasher=hashlib.sha256()
    for row in parts:
        check_cancelled()
        part=read('histpart_'+row['sha256']+'.bin',PART_BYTES)
        if len(part)!=row['size'] or hashlib.sha256(part).hexdigest()!=row['sha256']:
            raise ValueError('历史归档片段缺失或损坏，不能恢复完整记录。')
        payload.extend(part);hasher.update(part)
    if hasher.hexdigest()!=digest:raise ValueError('历史归档完整摘要不匹配。')
    record=json.loads(payload)
    if (not isinstance(record,dict) or record.get('reference')!=header['reference']
            or record.get('thread_key')!=header['thread_key'] or record.get('schema_version')!=2
            or record.get('archive_truncated') is not False):
        raise ValueError('历史归档片段与清单身份不一致。')
    return record
