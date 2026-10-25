"""Disposable disk indexes must be reclaimed even without Python finally."""
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from nailong.core.input_snapshot import _Deadline
from nailong.core.snapshot_inventory import inventory_database
from nailong.core.verification_artifacts import artifact_index


CRASH_SCRIPT='''
import os,sys
from pathlib import Path
from nailong.core.input_snapshot import _Deadline
if sys.argv[1]=='inventory':
    from nailong.core.snapshot_inventory import inventory_database
    context=inventory_database(Path(sys.argv[2]),_Deadline())
else:
    from nailong.core.verification_artifacts import artifact_index
    context=artifact_index()
with context as index:
    for i in range(12000):
        path=Path(f'group-{i}')/('x'*200)/('y'*200)/('z'*200)
        if sys.argv[1]=='inventory':index.file('before',path,(1,2,3))
        else:index.add(path)
    index.connection.commit()
    table='files' if sys.argv[1]=='inventory' else 'paths'
    assert index.connection.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==12000
    os._exit(81)
'''


class TransientIndexLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name).resolve();self.root=self.base/'project';self.root.mkdir()

    def crash(self,kind):
        directory=self.base/kind;directory.mkdir()
        environment={**os.environ,**{name:str(directory) for name in ('TMPDIR','SQLITE_TMPDIR','TMP','TEMP')}}
        result=subprocess.run([sys.executable,'-B','-c',CRASH_SCRIPT,kind,str(self.root)],
            env=environment,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,81,result.stderr)
        files=[p for p in directory.rglob('*') if p.is_file()]
        self.assertEqual(files,[],f'{kind} crash left {sum(p.stat().st_size for p in files)} bytes')

    def test_inventory_native_hard_exit_leaves_no_named_database(self):self.crash('inventory')

    def test_artifact_index_native_hard_exit_leaves_no_named_database(self):self.crash('artifact')

    def contexts(self):
        return (('inventory',lambda:inventory_database(self.root,_Deadline())),('artifact',artifact_index))

    def test_data_and_budgets_belong_to_scratch_not_empty_main(self):
        for kind,context in self.contexts():
            with self.subTest(kind=kind),context() as index:
                connection=index.connection
                self.assertEqual(connection.execute('PRAGMA scratch.max_page_count').fetchone()[0],131072)
                self.assertEqual(connection.execute('PRAGMA scratch.cache_size').fetchone()[0],-2048)
                self.assertEqual(connection.execute('PRAGMA scratch.page_size').fetchone()[0],4096)
                self.assertEqual(connection.execute('PRAGMA temp_store').fetchone()[0],1)
                self.assertEqual(connection.execute('PRAGMA main.page_count').fetchone()[0],0)
                names={row[0] for row in connection.execute('SELECT name FROM scratch.sqlite_master')}
                self.assertIn('files' if kind=='inventory' else 'paths',names)
                self.assertTrue(all(row[2]=='' for row in connection.execute('PRAGMA database_list')))

    @unittest.skipUnless(os.name=='posix' and Path('/dev/fd').exists(),'native POSIX disk handle observation')
    def test_large_live_indexes_use_private_unlinked_disk_files(self):
        for kind,context in self.contexts():
            with self.subTest(kind=kind),context() as index:
                for i in range(6000):
                    path=Path(str(i))/('x'*200)/('y'*200)/('z'*200)
                    if kind=='inventory':index.file('before',path,(1,2,3))
                    else:index.add(path)
                index.connection.commit()
                self.assertGreater(index.connection.execute('PRAGMA scratch.page_count').fetchone()[0]*4096,2*1024*1024)
                live=[]
                for raw in os.listdir('/dev/fd'):
                    try:
                        value=os.fstat(int(raw))
                        if stat.S_ISREG(value.st_mode) and value.st_nlink==0 and value.st_size>2*1024*1024:
                            live.append(value)
                    except OSError:pass
                self.assertTrue(live,'large index did not expose any anonymous disk storage')
                self.assertTrue(all(stat.S_IMODE(value.st_mode)==0o600 for value in live))

    def test_normal_and_exceptional_scope_exit_close_real_connections(self):
        for kind,context in self.contexts():
            with self.subTest(kind=kind):
                with context() as index:connection=index.connection
                with self.assertRaises(sqlite3.ProgrammingError):connection.execute('SELECT 1')
                with self.assertRaisesRegex(RuntimeError,'consumer failure'):
                    with context() as index:
                        connection=index.connection
                        raise RuntimeError('consumer failure')
                with self.assertRaises(sqlite3.ProgrammingError):connection.execute('SELECT 1')

    def test_sql_policy_is_configured_before_data_bearing_database_opens(self):
        import nailong.core.ephemeral_sqlite as scratch
        native=sqlite3.connect;statements=[]
        def observed(*args,**kwargs):
            connection=native(*args,**kwargs);connection.set_trace_callback(statements.append);return connection
        before=dict(os.environ);cwd=Path.cwd()
        with patch.object(scratch.sqlite3,'connect',side_effect=observed),artifact_index() as index:
            index.add(Path('native-path'))
        self.assertLess(statements.index('PRAGMA temp_store=FILE'),statements.index("ATTACH DATABASE '' AS scratch"))
        self.assertTrue(any(row.startswith('CREATE TABLE scratch.paths') for row in statements))
        self.assertEqual(dict(os.environ),before);self.assertEqual(Path.cwd(),cwd)
        self.assertFalse(any('temp_store_directory' in row for row in statements))

    def test_python_temp_directory_inside_input_is_still_rejected(self):
        import nailong.core.ephemeral_sqlite as scratch
        with patch.object(scratch.tempfile,'gettempdir',return_value=str(self.root)):
            with self.assertRaisesRegex(ValueError,'项目输入'):
                with inventory_database(self.root,_Deadline()):self.fail('unsafe index admitted')

    @unittest.skipIf(os.name=='nt','SQLite Windows VFS uses GetTempPath, not SQLITE_TMPDIR')
    def test_sqlite_temp_directory_inside_input_is_rejected_even_when_python_is_outside(self):
        import nailong.core.ephemeral_sqlite as scratch
        with patch.dict(os.environ,{'SQLITE_TMPDIR':str(self.root)}),patch.object(scratch.tempfile,'gettempdir',return_value=str(self.base)):
            with self.assertRaisesRegex(ValueError,'项目输入'):
                with inventory_database(self.root,_Deadline()):self.fail('SQLite location bypassed input guard')

    @unittest.skipUnless(os.name=='nt','native GetTempPathW location guard')
    def test_windows_native_temp_path_inside_input_is_rejected(self):
        import nailong.core.ephemeral_sqlite as scratch
        with patch.dict(os.environ,{'TMP':str(self.root)}),patch.object(scratch.tempfile,'gettempdir',return_value=str(self.base)):
            self.assertEqual(scratch._windows_temp_directory().resolve(),self.root)
            with self.assertRaisesRegex(ValueError,'项目输入'):
                with inventory_database(self.root,_Deadline()):self.fail('Windows temp path bypassed guard')

    def test_forced_memory_build_policy_fails_before_attach_and_closes(self):
        import nailong.core.ephemeral_sqlite as scratch
        native=sqlite3.connect;created=[];statements=[]
        class MemoryBuild:
            def __init__(self):
                self.connection=native('');self.connection.set_trace_callback(statements.append);created.append(self.connection)
            def execute(self,sql,*args):
                return [('TEMP_STORE=3',)] if sql=='PRAGMA compile_options' else self.connection.execute(sql,*args)
            def close(self):self.connection.close()
        with patch.object(scratch.sqlite3,'connect',side_effect=lambda *a,**k:MemoryBuild()):
            with self.assertRaisesRegex(ValueError,'未回退到内存'):
                with scratch.scratch_database(_Deadline(),max_pages=131072):self.fail('memory-only build admitted')
        self.assertFalse(any(row.startswith('ATTACH') for row in statements))
        with self.assertRaises(sqlite3.ProgrammingError):created[0].execute('SELECT 1')

    def test_native_page_capacity_error_closes_connection_and_never_yields_partial_index(self):
        from nailong.core.ephemeral_sqlite import scratch_database
        connection=None
        with self.assertRaises(sqlite3.OperationalError):
            with scratch_database(_Deadline(),max_pages=1) as connection:
                connection.execute('CREATE TABLE scratch.paths(path BLOB PRIMARY KEY)')
        self.assertIsNotNone(connection)
        with self.assertRaises(sqlite3.ProgrammingError):connection.execute('SELECT 1')

    def test_cancelled_queries_close_both_index_connections(self):
        import threading
        from nailong.core.input_snapshot import active_snapshot_cancellation
        for kind,context in self.contexts():
            with self.subTest(kind=kind):
                event=threading.Event();token=active_snapshot_cancellation.set(event);connection=None
                try:
                    with self.assertRaises(ValueError):
                        with context() as index:
                            connection=index.connection;event.set()
                            connection.execute('WITH RECURSIVE numbers(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM numbers WHERE x<1000000) SELECT sum(x) FROM numbers').fetchone()
                finally:active_snapshot_cancellation.reset(token)
                with self.assertRaises(sqlite3.ProgrammingError):connection.execute('SELECT 1')

    def test_unused_main_cannot_accept_unqualified_data_tables(self):
        with artifact_index() as index:
            with self.assertRaises(sqlite3.OperationalError):
                index.connection.execute('CREATE TABLE accidental_main_data(payload BLOB)')
            self.assertEqual(index.connection.execute('PRAGMA main.page_count').fetchone()[0],0)
            self.assertEqual(index.connection.execute('PRAGMA main.max_page_count').fetchone()[0],1)

    def test_legacy_migration_tables_share_the_bounded_scratch_database(self):
        from nailong.core.output_migration import migrate_outputs
        with artifact_index() as index:
            self.assertEqual(migrate_outputs(None,self.root,[],None,index),[])
            names={row[0] for row in index.connection.execute('SELECT name FROM scratch.sqlite_master')}
            self.assertTrue({'legacy_claims','legacy_scopes'}.issubset(names))
            self.assertEqual(index.connection.execute('PRAGMA main.page_count').fetchone()[0],0)

    def test_parallel_private_connections_do_not_share_rows(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        gate=threading.Barrier(2)
        def enter(name):
            with artifact_index() as index:
                index.add(Path(name));gate.wait(timeout=3)
                return [str(path) for path in index]
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(enter,('first','second'))),[['first'],['second']])
