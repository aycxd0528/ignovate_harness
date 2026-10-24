"""Immutable, bounded artifact receipts. Display previews never grant exclusions."""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile

from nailong.core.safe_files import pinned_directory
from nailong.core.input_snapshot import _Deadline, _identity_entry
from nailong.tools.files import FileSession
from nailong.core.artifact_storage import METADATA_BYTES, publish_manifest
from nailong.core.artifact_directory import read_bytes,write_bytes

CHUNK_BYTES = 1024 * 1024
MAX_BYTES = 64 * CHUNK_BYTES
_HEX = re.compile(r'[a-f0-9]{64}\Z')


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()


def _path(raw):
    if (not isinstance(raw, str) or not raw or '\0' in raw or Path(raw).anchor
            or not Path(raw).parts or '..' in Path(raw).parts
            or raw not in {str(Path(raw)), Path(raw).as_posix()}
            or any(FileSession._is_protected(p) for p in Path(raw).parts)
            or Path(raw).parts[0] in {'.nailong', 'AGENTS.md', 'CLAUDE.md'}):
        raise ValueError('产物清单路径无效。')
    return Path(raw)


def _directory(boundary):
    return Path(boundary) / 'verification-artifacts-v1'


def validate_reference(reference):
    if (not isinstance(reference, dict) or set(reference) != {'version', 'id'}
            or type(reference['version']) is not int or reference['version'] != 1
            or not isinstance(reference['id'], str) or not _HEX.fullmatch(reference['id'])):
        raise ValueError('完整产物证明引用无效。')


def _read(path, limit):
    data = read_bytes(path,limit)
    if len(data) > limit:
        raise ValueError('产物清单超过容量限制。')
    return data


def _publish(boundary, data, suffix):
    identity = hashlib.sha256(data).hexdigest()
    path = _directory(boundary) / (identity + suffix)
    try:
        write_bytes(path, data, replace=False)
    except FileExistsError:
        if _read(path, len(data)) != data:
            raise ValueError('不可变产物清单已损坏。')
    return identity


class ManifestWriter:
    def __init__(self, boundary, binding):
        self.boundary = boundary
        self.binding = binding
        self.pending = bytearray()
        self.parts = []
        self.total = self.count = 0
        self.preview = []
        self.deadline = _Deadline()
        self.spool = tempfile.TemporaryFile()
        self.staged = []
        self.storage_warnings = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.spool.close()
        self.pending.clear()

    def add(self, path, kind, mode, payload):
        if self.spool.closed:raise ValueError('产物观察已关闭。')
        self.deadline.check()
        row = _json([str(path), kind, mode, payload]) + b'\n'
        if len(row) > CHUNK_BYTES or self.total + len(row) > MAX_BYTES:
            raise ValueError('完整产物清单超过 64 MiB；未发布部分证明。')
        if len(self.pending) + len(row) > CHUNK_BYTES:
            self._flush()
        self.pending.extend(row)
        self.total += len(row)
        self.count += 1
        if len(self.preview) < 64:
            self.preview.append(str(path))

    def _flush(self):
        if self.pending:
            data = bytes(self.pending)
            identity=hashlib.sha256(data).hexdigest()
            self.staged.append((self.spool.tell(),identity,len(data)))
            self.spool.write(data)
            self.parts.append([identity, len(data)])
            self.pending.clear()

    def finish(self, snapshot):
        self.deadline.check()
        self._flush()
        if self.count != snapshot.artifact_count:
            raise ValueError('产物清单条目不完整。')
        header = {'version': 1, **self.binding, 'parts': self.parts, 'bytes': self.total,
                  'count': self.count, 'artifacts': snapshot.artifacts}
        identity,maintenance=publish_manifest(self.boundary,_json(header),self.spool,self.staged,_publish)
        self.storage_warnings=maintenance['warnings']
        return {'version': 1, 'id': identity}


class ArtifactIndex:
    disk_membership = True

    def __init__(self, connection, deadline):
        self.connection = connection
        self.deadline = deadline
        self.bytes = 0
        self.header = None

    def add(self, path, *, directory=False):
        self.deadline.check()
        key = os.fsencode(path)
        cursor = self.connection.execute('INSERT OR IGNORE INTO paths VALUES (?,?,0)', (key, int(directory)))
        self.bytes += (len(key) + 32) * cursor.rowcount
        if self.bytes > MAX_BYTES:
            raise ValueError('完整产物路径清单超过 64 MiB。')

    def __contains__(self, path):
        self.deadline.check()
        return self.connection.execute('SELECT 1 FROM paths WHERE path=?', (os.fsencode(path),)).fetchone() is not None

    def __iter__(self):
        for row in self.connection.execute('SELECT path FROM paths ORDER BY path'):
            self.deadline.check()
            yield Path(os.fsdecode(row[0]))

    def next_directory(self):
        row = self.connection.execute('SELECT path FROM paths WHERE directory=1 AND processed=0 ORDER BY path LIMIT 1').fetchone()
        if row is None:
            return None
        self.connection.execute('UPDATE paths SET processed=1 WHERE path=?', row)
        return Path(os.fsdecode(row[0]))


@contextmanager
def artifact_index():
    deadline = _Deadline()
    from nailong.core.ephemeral_sqlite import scratch_database
    try:
        with scratch_database(deadline,max_pages=131072) as connection:
            connection.execute('CREATE TABLE scratch.paths(path BLOB PRIMARY KEY,directory INTEGER,processed INTEGER)')
            connection.execute('CREATE INDEX scratch.pending ON paths(directory,processed,path)')
            yield ArtifactIndex(connection,deadline)
    except sqlite3.Error as error:
        raise ValueError('完整产物索引无法完成。') from error


def enumerate_generated(root, declared, index):
    root = Path(root)
    def add(relative):
        _path(str(relative))
        try:
            context = pinned_directory((root / relative).parent)
            handle = context.__enter__()
        except FileNotFoundError:
            return
        try:
            try:
                value = (root / relative).lstat()
            except FileNotFoundError:
                return
            if getattr(value, 'st_file_attributes', 0) & 0x400:
                raise ValueError('产物包含未知 Windows 重解析点。')
            if not (stat.S_ISREG(value.st_mode) or stat.S_ISDIR(value.st_mode) or stat.S_ISLNK(value.st_mode)):
                raise ValueError('产物包含非普通文件。')
            index.add(relative, directory=stat.S_ISDIR(value.st_mode))
        finally:
            context.__exit__(None, None, None)
    for raw in declared:
        add(_path(raw))
    while (relative := index.next_directory()) is not None:
        index.deadline.check()
        with pinned_directory(root / relative) as handle:
            with os.scandir(root / relative if os.name == 'nt' else handle) as entries:
                for entry in entries:
                    add(relative / entry.name)


def add_generated_parents(root, parents, index):
    for relative in parents:
        index.deadline.check()
        try:
            with pinned_directory(Path(root) / relative):
                # Metadata only; unknown siblings remain ordinary inputs.
                index.add(relative)
        except FileNotFoundError:
            continue


def load_manifest(boundary, reference, project, index, *, copy_to=None):
    try:
        with ExitStack() as stack:
            spool=stack.enter_context(tempfile.TemporaryFile()) if copy_to is not None else None
            header,data,staged=_load_manifest(boundary, reference, project, index, spool=spool)
            if copy_to is not None:
                publish_manifest(copy_to,data,spool,staged,_publish)
            return header
    except (TypeError, KeyError, IndexError, AttributeError, UnicodeError, RecursionError, json.JSONDecodeError) as error:
        raise ValueError('完整产物证明结构无效。') from error


def _load_manifest(boundary, reference, project, index, *, spool=None):
    validate_reference(reference)
    directory = _directory(boundary)
    data = _read(directory / (reference['id'] + '.json'), METADATA_BYTES)
    if hashlib.sha256(data).hexdigest() != reference['id']:
        raise ValueError('完整产物证明损坏。')
    header = json.loads(data)
    if (type(header.get('version')) is not int or header['version'] != 1 or header.get('project') != str(Path(project).resolve())
            or not isinstance(header.get('parts'), list) or len(header['parts']) > 128
            or type(header.get('count')) is not int or header['count'] < 0
            or type(header.get('bytes')) is not int or not 0 <= header['bytes'] <= MAX_BYTES
            or not isinstance(header.get('artifacts'), str) or not _HEX.fullmatch(header['artifacts'])):
        raise ValueError('完整产物证明头无效。')
    digest = hashlib.sha256(b'ignovate-artifacts-v1\0')
    total = count = 0
    staged=[]
    for identity, size in header['parts']:
        index.deadline.check()
        if not isinstance(identity, str) or not _HEX.fullmatch(identity) or type(size) is not int or not 0 < size <= CHUNK_BYTES:
            raise ValueError('产物片段引用无效。')
        part = _read(directory / (identity + '.part'), size)
        total += len(part)
        if len(part) != size or total > MAX_BYTES or hashlib.sha256(part).hexdigest() != identity or not part.endswith(b'\n'):
            raise ValueError('完整产物片段缺失或损坏。')
        for line in part.splitlines():
            path, kind, mode, payload = json.loads(line)
            relative = _path(path)
            if (kind not in {'directory', 'file', 'link'} or type(mode) is not int or not 0 <= mode <= 0o7777
                    or not isinstance(payload, str) or (kind == 'directory' and payload != '')
                    or (kind != 'directory' and not _HEX.fullmatch(payload)) or relative in index):
                raise ValueError('产物条目无效或重复。')
            index.add(relative)
            _identity_entry(digest, kind.encode(), relative, mode, bytes.fromhex(payload))
            count += 1
        if spool is not None:
            staged.append((spool.tell(),identity,size))
            spool.write(part)
    if total != header['bytes'] or count != header['count'] or digest.hexdigest() != header['artifacts']:
        raise ValueError('产物清单未完整覆盖。')
    index.header = header
    return header,data,staged


def copy_manifest(boundary, reference, project, destination):
    with artifact_index() as index:
        load_manifest(boundary, reference, project, index, copy_to=destination)


def evidence_identity(inputs, artifacts):
    return hashlib.sha256(b'ignovate-current-evidence-v1\0' + bytes.fromhex(inputs) + bytes.fromhex(artifacts)).hexdigest()
