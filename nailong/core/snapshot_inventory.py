"""Private, disposable disk inventories; never persistent verification proof."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
from nailong.core.ephemeral_sqlite import scratch_database

MAX_INDEX_PAGES = 131_072  # 512 MiB of metadata, independent of input byte size.


@contextmanager
def inventory_database(root, deadline):
    try:
        with scratch_database(deadline,max_pages=MAX_INDEX_PAGES,excluded_root=root) as connection:
            connection.execute('CREATE TABLE scratch.files (phase TEXT, path BLOB, directory BLOB, name BLOB, '
                               'version TEXT, PRIMARY KEY (phase,path))')
            connection.execute('CREATE INDEX scratch.file_order ON files(phase,directory,name)')
            connection.execute('CREATE TABLE scratch.dirs (phase TEXT, path BLOB, '
                               'version TEXT, covered INTEGER, processed INTEGER DEFAULT 0, '
                               'PRIMARY KEY (phase,path))')
            connection.execute('CREATE INDEX scratch.dir_queue ON dirs(phase,processed,path)')
            yield SnapshotInventory(connection)
    except sqlite3.Error as error:
        deadline.check()
        raise ValueError('无法完整生成验证摘要临时索引（元数据上限 512 MiB 或存储不可用）。') from error


class SnapshotInventory:
    def __init__(self, connection):
        self.connection = connection

    def file(self, phase, path, version):
        # Component separators preserve os.walk's parent-before-child order,
        # including Unicode and undecodable POSIX filenames.
        directory = b''.join(part.encode('utf-8', 'surrogatepass') + b'\0' for part in path.parts[:-1])
        name = path.name.encode('utf-8', 'surrogatepass')
        self.connection.execute('INSERT INTO files VALUES (?,?,?,?,?)',
            (phase, os.fsencode(path), directory, name, json.dumps(version)))

    @staticmethod
    def path_key(path):
        return os.fsencode(path) if path.parts else b''

    def directory(self, phase, path, version, *, covered=True):
        self.connection.execute('INSERT INTO dirs(phase,path,version,covered) VALUES (?,?,?,?)',
            (phase, self.path_key(path), json.dumps(version), int(covered)))

    def next_directory(self, phase):
        row = self.connection.execute('SELECT path,version FROM dirs WHERE phase=? AND processed=0 '
                                      'ORDER BY path LIMIT 1', (phase,)).fetchone()
        if row is None:
            return None
        self.connection.execute('UPDATE dirs SET processed=1 WHERE phase=? AND path=?', (phase, row[0]))
        return Path(os.fsdecode(row[0])), tuple(json.loads(row[1]))

    def directory_entries(self, phase):
        for path, version, covered in self.connection.execute(
                'SELECT path,version,covered FROM dirs WHERE phase=? ORDER BY path', (phase,)):
            yield Path(os.fsdecode(path)), tuple(json.loads(version)), bool(covered)

    def lookup(self, phase, path):
        key = self.path_key(path)
        row = self.connection.execute('SELECT version FROM dirs WHERE phase=? AND path=?', (phase, key)).fetchone()
        if row is None:
            row = self.connection.execute('SELECT version FROM files WHERE phase=? AND path=?', (phase, key)).fetchone()
        return tuple(json.loads(row[0])) if row else None

    def files(self, phase):
        for path, version in self.connection.execute(
                'SELECT path,version FROM files WHERE phase=? ORDER BY directory,name', (phase,)):
            yield Path(os.fsdecode(path)), tuple(json.loads(version))

    def unchanged(self):
        for table in ('files', 'dirs'):
            for first, second in (('before', 'after'), ('after', 'before')):
                changed = self.connection.execute(
                    f'SELECT 1 FROM {table} a LEFT JOIN {table} b ON b.phase=? AND a.path=b.path '
                    'WHERE a.phase=? AND (b.path IS NULL OR a.version!=b.version) LIMIT 1',
                    (second, first)).fetchone()
                if changed:
                    return False
        return True
