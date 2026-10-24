"""Immutable history publication has crash recovery and conservative collection."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from langchain_core.messages import HumanMessage
from nailong.core.history_archive import HistoryArchive,MAX_ARCHIVE_BYTES


class HistoryStorageTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.archive=HistoryArchive(self.root/'history')

    def parts(self):return {path.name for path in self.archive.root.rglob('histpart_*.bin')}

    def test_repeated_final_header_failures_do_not_keep_unreferenced_parts(self):
        prior=HumanMessage(content='completed '*300000+'PRIOR_COMPLETE_TAIL')
        reference=self.archive.save('one',prior);before=self.parts()
        native=self.archive._publish
        def fail_head(directory,name,payload):
            if name.endswith('.json'):raise OSError('native final header failure')
            return native(directory,name,payload)
        with patch.object(self.archive,'_publish',side_effect=fail_head):
            for index in range(5):
                with self.assertRaises(ValueError):
                    self.archive.save('one',HumanMessage(content=chr(65+index)*(MAX_ARCHIVE_BYTES+8000)))
        self.assertEqual(self.parts(),before)
        self.assertEqual(self.archive.read('one',reference,offset=len(prior.content)-19)['content'],'PRIOR_COMPLETE_TAIL')

    def test_failure_after_a_real_part_publish_cleans_only_uncommitted_data(self):
        reference=self.archive.save('one',HumanMessage(content='valid small history'))
        native=self.archive._publish;seen=[]
        def fail_part(directory,name,payload):
            native(directory,name,payload)
            if name.endswith('.bin'):
                seen.append(name)
                raise OSError('native part exists but next stage failed')
        with patch.object(self.archive,'_publish',side_effect=fail_part):
            with self.assertRaises(ValueError):self.archive.save('one',HumanMessage(content='x'*(MAX_ARCHIVE_BYTES+8000)))
        self.assertTrue(seen)
        self.assertEqual(self.parts(),set())
        self.assertEqual(self.archive.read('one',reference)['content'],'valid small history')

    def directory(self):return self.archive.root/hashlib.sha256(b'one').hexdigest()

    def orphan(self,content=b'legacy orphan'):
        directory=self.directory();directory.mkdir(parents=True,exist_ok=True)
        path=directory/('histpart_'+hashlib.sha256(content).hexdigest()+'.bin');path.write_bytes(content)
        return path

    def test_first_maintenance_and_explicit_collection_keep_all_completed_roots(self):
        from nailong.core.history_storage import MAINTENANCE
        small=self.archive.save('one',HumanMessage(content='valid small'))
        large=self.archive.save('one',HumanMessage(content='L'*(MAX_ARCHIVE_BYTES+8000)+'COMPLETE_TAIL'))
        before=self.parts();orphan=self.orphan();note=self.directory()/'user-note.txt';note.write_text('keep')
        (self.directory()/MAINTENANCE).unlink()
        self.archive.save('one',HumanMessage(content='new healthy history'))
        self.assertFalse(orphan.exists());self.assertEqual(self.parts(),before)
        self.assertEqual(note.read_text(),'keep')
        self.assertEqual(self.archive.read('one',small)['content'],'valid small')
        self.assertEqual(self.archive.read('one',large,offset=MAX_ARCHIVE_BYTES+8000)['content'],'COMPLETE_TAIL')
        self.assertEqual(self.archive.collect_orphan_parts('one')['removed_parts'],0)

    def test_failed_new_header_keeps_a_repaired_part_referenced_by_old_header(self):
        content='x'*(MAX_ARCHIVE_BYTES+8000)+'SHARED_TAIL'
        reference=self.archive.save('one',HumanMessage(id='prior',content=content))
        header=json.loads((self.directory()/(reference+'.json')).read_bytes())
        restored=self.directory()/('histpart_'+header['parts'][1]['sha256']+'.bin');restored.unlink()
        native=self.archive._publish
        def fail_head(directory,name,payload):
            if name.endswith('.json'):raise OSError('new head fails after restoring shared part')
            return native(directory,name,payload)
        with patch.object(self.archive,'_publish',side_effect=fail_head),self.assertRaises(ValueError):
            self.archive.save('one',HumanMessage(id='retry',content=content))
        self.assertTrue(restored.exists())
        self.assertEqual(self.archive.read('one',reference,offset=len(content)-11)['content'],'SHARED_TAIL')
        self.assertEqual(self.parts(),{'histpart_'+row['sha256']+'.bin' for row in header['parts']})

    def test_corrupt_intent_is_never_reset_or_used_to_delete(self):
        from nailong.core.history_storage import INTENT
        self.archive.save('one',HumanMessage(content='valid'));orphan=self.orphan()
        path=self.directory()/INTENT;path.write_bytes(b'broken intent')
        for action in (lambda:self.archive.collect_orphan_parts('one'),lambda:self.archive.save('one',HumanMessage(content='new'))):
            with self.assertRaises(ValueError):action()
            self.assertEqual(path.read_bytes(),b'broken intent');self.assertTrue(orphan.exists())

    def test_bad_head_graph_keeps_candidates_and_warns_while_healthy_writes_work(self):
        self.archive.save('one',HumanMessage(content='valid'));orphan=self.orphan()
        (self.directory()/('hist_'+'0'*64+'.json')).write_bytes(b'broken head')
        result=self.archive.collect_orphan_parts('one')
        self.assertEqual(result['removed_parts'],0);self.assertTrue(result['warnings']);self.assertTrue(orphan.exists())
        reference=self.archive.save('one',HumanMessage(content='healthy new history'))
        self.assertTrue(self.archive.storage_warnings);self.assertLessEqual(len(self.archive.storage_warnings),8)
        self.assertEqual(self.archive.read('one',reference)['content'],'healthy new history')

    def test_graph_and_membership_capacity_never_delete_from_partial_reference_set(self):
        import nailong.core.history_storage as storage
        self.archive.save('one',HumanMessage(content='v'*(MAX_ARCHIVE_BYTES+8000)))
        self.orphan();before=self.parts()
        for target in ('graph','membership'):
            with self.subTest(target=target):
                scope=patch.object(storage,'MAX_GRAPH_BYTES',1) if target=='graph' else patch('nailong.core.verification_artifacts.MAX_BYTES',1)
                with scope:result=self.archive.collect_orphan_parts('one')
                self.assertTrue(result['warnings']);self.assertEqual(self.parts(),before)
        result=self.archive.collect_orphan_parts('one')
        self.assertEqual(result['removed_parts'],1)

    def test_healthy_modern_cache_hits_and_new_records_skip_full_graph_scan(self):
        import nailong.core.history_storage as storage
        message=HumanMessage(content='valid');reference=self.archive.save('one',message)
        with patch.object(storage,'_collect',side_effect=AssertionError('unexpected full history scan')):
            self.assertEqual(self.archive.save('one',message),reference)
            self.archive.save('one',HumanMessage(content='another healthy history'))

    def test_committed_head_survives_journal_cleanup_failure_and_next_owner(self):
        from nailong.core.artifact_directory import ArtifactDirectory
        from nailong.core.history_storage import INTENT
        native=ArtifactDirectory.unlink
        def fail_cleanup(owner,name):
            if name==INTENT:raise OSError('post-commit journal cleanup fails')
            return native(owner,name)
        message=HumanMessage(content='x'*(MAX_ARCHIVE_BYTES+8000)+'COMPLETE_TAIL')
        with patch.object(ArtifactDirectory,'unlink',fail_cleanup):reference=self.archive.save('one',message)
        header=self.directory()/(reference+'.json');before=header.read_bytes()
        self.assertTrue(self.archive.storage_warnings);self.assertTrue((self.directory()/INTENT).exists())
        self.assertEqual(HistoryArchive(self.archive.root).save('one',message),reference)
        self.assertFalse((self.directory()/INTENT).exists());self.assertEqual(header.read_bytes(),before)
        self.assertEqual(self.archive.read('one',reference,offset=len(message.content)-13)['content'],'COMPLETE_TAIL')

    def test_committed_retry_succeeds_when_journal_cleanup_keeps_failing(self):
        from nailong.core.artifact_directory import ArtifactDirectory
        from nailong.core.history_storage import INTENT
        native=ArtifactDirectory.unlink
        def fail_cleanup(owner,name):
            if name==INTENT:raise OSError('journal cleanup remains unavailable')
            return native(owner,name)
        message=HumanMessage(content='committed source must remain usable')
        with patch.object(ArtifactDirectory,'unlink',fail_cleanup):
            reference=self.archive.save('one',message)
            journal=(self.directory()/INTENT).read_bytes()
            self.assertEqual(self.archive.save('one',message),reference)
            self.assertEqual(self.archive.read('one',reference)['content'],message.content)
            with self.assertRaises(ValueError):self.archive.save('one',HumanMessage(content='different publication'))
            self.assertEqual((self.directory()/INTENT).read_bytes(),journal)
            self.assertEqual(len(list(self.directory().glob('hist_*.json'))),1)

    @unittest.skipIf(os.name=='nt','native POSIX directory rename and symlink')
    def test_collection_directory_replacement_never_mutates_replacement(self):
        import shutil
        import nailong.core.history_storage as storage
        self.archive.save('one',HumanMessage(content='valid'));orphan=self.orphan()
        directory=self.directory();moved=directory.with_name('moved');native=storage._head_parts;swapped=False
        def swap(data,reference,key):
            nonlocal swapped
            result=native(data,reference,key)
            if not swapped:
                swapped=True;directory.rename(moved);shutil.copytree(moved,directory)
                (directory/orphan.name).write_bytes(b'replacement user data')
            return result
        with patch.object(storage,'_head_parts',side_effect=swap),self.assertRaises(ValueError):
            self.archive.collect_orphan_parts('one')
        self.assertTrue(swapped);self.assertEqual((directory/orphan.name).read_bytes(),b'replacement user data')
        self.assertFalse((moved/orphan.name).exists())

    @unittest.skipIf(os.name=='nt','native POSIX directory rename')
    def test_publication_directory_replacement_does_not_publish_into_new_store(self):
        self.archive.save('one',HumanMessage(content='valid'))
        directory=self.directory();moved=directory.with_name('moved');native=self.archive._publish;swapped=False
        def swap(handle,name,payload):
            nonlocal swapped
            native(handle,name,payload)
            if name.endswith('.bin') and not swapped:
                swapped=True;directory.rename(moved);directory.mkdir()
                (directory/name).write_bytes(b'replacement user data')
        with patch.object(self.archive,'_publish',side_effect=swap),self.assertRaises(ValueError):
            self.archive.save('one',HumanMessage(content='x'*(MAX_ARCHIVE_BYTES+8000)))
        self.assertTrue(swapped)
        self.assertEqual([p.read_bytes() for p in directory.iterdir()],[b'replacement user data'])

    @unittest.skipIf(os.name=='nt','native POSIX symlink semantics')
    def test_link_candidate_is_never_followed_or_deleted(self):
        self.archive.save('one',HumanMessage(content='valid'))
        outside=self.root/'outside';outside.write_bytes(b'external bytes')
        link=self.directory()/('histpart_'+'a'*64+'.bin');link.symlink_to(outside)
        result=self.archive.collect_orphan_parts('one')
        self.assertTrue(result['warnings']);self.assertTrue(link.is_symlink())
        self.assertEqual(outside.read_bytes(),b'external bytes')

    def test_native_process_exit_before_or_after_header_recovers_correctly(self):
        import subprocess,sys
        from nailong.core.history_storage import INTENT
        script='''
import os,sys
from pathlib import Path
from langchain_core.messages import HumanMessage
from nailong.core.history_archive import HistoryArchive,MAX_ARCHIVE_BYTES
archive=HistoryArchive(Path(sys.argv[1]));native=archive._publish
stage=sys.argv[2]
def crash(directory,name,payload):
    native(directory,name,payload)
    if stage=='part' and name.endswith('.bin') or stage=='head' and name.endswith('.json'):os._exit(79)
archive._publish=crash
archive.save('one',HumanMessage(content='x'*(MAX_ARCHIVE_BYTES+8000)+'CRASH_TAIL'))
'''
        for stage in ('part','head'):
            with self.subTest(stage=stage):
                root=self.root/stage
                result=subprocess.run([sys.executable,'-B','-c',script,str(root),stage],capture_output=True,text=True,timeout=12)
                self.assertEqual(result.returncode,79,result.stderr)
                archive=HistoryArchive(root);directory=root/hashlib.sha256(b'one').hexdigest()
                self.assertTrue((directory/INTENT).exists())
                before={p.name:p.read_bytes() for p in directory.glob('hist_*.json')}
                collected=archive.collect_orphan_parts('one')
                self.assertFalse((directory/INTENT).exists())
                if stage=='part':
                    self.assertGreater(collected['removed_parts'],0)
                    self.assertEqual(list(directory.glob('histpart_*.bin')),[])
                else:
                    self.assertEqual(collected['removed_parts'],0)
                    self.assertEqual({p.name:p.read_bytes() for p in directory.glob('hist_*.json')},before)
                    reference=next(iter(before))[:-5]
                    self.assertEqual(archive.read('one',reference,offset=MAX_ARCHIVE_BYTES+8000)['content'],'CRASH_TAIL')

    def test_native_process_publisher_and_collector_serialize(self):
        import subprocess,sys
        from concurrent.futures import ThreadPoolExecutor
        script='''
import sys,time
from pathlib import Path
from langchain_core.messages import HumanMessage
from nailong.core.history_archive import HistoryArchive,MAX_ARCHIVE_BYTES
archive=HistoryArchive(Path(sys.argv[1]));native=archive._publish;paused=False
release=Path(sys.argv[2])
def gated(directory,name,payload):
    global paused
    native(directory,name,payload)
    if name.endswith('.bin') and not paused:
        paused=True;print('READY',flush=True);until=time.monotonic()+5
        while not release.exists():
            if time.monotonic()>until:raise TimeoutError('parent did not release publisher')
            time.sleep(.01)
archive._publish=gated
reference=archive.save('one',HumanMessage(content='x'*(MAX_ARCHIVE_BYTES+8000)+'PROCESS_TAIL'))
print(reference,flush=True)
'''
        release=self.root/'release'
        process=subprocess.Popen([sys.executable,'-B','-c',script,str(self.archive.root),str(release)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                ready=pool.submit(process.stdout.readline)
                self.assertEqual(ready.result(timeout=3).strip(),'READY')
                collected=pool.submit(self.archive.collect_orphan_parts,'one')
                import time;time.sleep(.05)
                self.assertFalse(collected.done(),'collector entered while publisher still owns transaction')
                release.touch()
                self.assertEqual(process.wait(timeout=6),0,process.stderr.read())
                self.assertEqual(collected.result(timeout=3)['removed_parts'],0)
                reference=process.stdout.readline().strip()
                self.assertEqual(self.archive.read('one',reference,offset=MAX_ARCHIVE_BYTES+8000)['content'],'PROCESS_TAIL')
        finally:
            release.touch()
            if process.poll() is None:process.kill();process.wait(timeout=3)
            process.stdout.close();process.stderr.close()


    def test_structurally_valid_but_corrupt_roots_never_authorize_collection(self):
        reference=self.archive.save('one',HumanMessage(content='valid small'))
        small=self.directory()/(reference+'.json');original=small.read_bytes()
        header=json.loads(original);header['message']['data']['content']='tampered content';small.write_text(json.dumps(header))
        orphan=self.orphan();result=self.archive.collect_orphan_parts('one')
        self.assertTrue(result['warnings']);self.assertTrue(orphan.exists())
        small.write_bytes(original)
        reference=self.archive.save('one',HumanMessage(content='x'*(MAX_ARCHIVE_BYTES+8000)))
        large=self.directory()/(reference+'.json');header=json.loads(large.read_bytes());header['sha256']='0'*64
        large.write_text(json.dumps(header));result=self.archive.collect_orphan_parts('one')
        self.assertTrue(result['warnings']);self.assertTrue(orphan.exists())



class HistoryStorageAsyncTests(unittest.IsolatedAsyncioTestCase):
    setUp=HistoryStorageTests.setUp

    async def test_repeated_cancel_while_waiting_for_native_lock_joins_and_releases(self):
        import asyncio,threading
        import nailong.core.history_storage as storage
        from nailong.core.artifact_directory import ArtifactDirectory
        from nailong.core.history_archive import _close_directory
        from nailong.core.input_snapshot import _Deadline
        from nailong.core.verification import _owned_worker
        self.archive.save('one',HumanMessage(content='prior valid'))
        entered=threading.Event();release=threading.Event();waiting=threading.Event()
        key=self.archive._thread_key('one')
        def hold():
            directory=self.archive._thread_directory(key,create=True)
            try:
                owner=ArtifactDirectory(self.archive.root/key,directory.handle if os.name=='nt' else directory)
                with storage._locked(owner,_Deadline()):entered.set();release.wait(5)
            finally:_close_directory(directory)
        holder=asyncio.create_task(asyncio.to_thread(hold));pending=None
        native_sleep=storage.time.sleep
        def wait_signal(seconds):waiting.set();native_sleep(seconds)
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait,2))
            with patch.object(storage.time,'sleep',side_effect=wait_signal):
                pending=asyncio.create_task(_owned_worker(self.archive.save,'one',HumanMessage(content='new history')))
                self.assertTrue(await asyncio.to_thread(waiting.wait,2))
                pending.cancel();await asyncio.sleep(0);pending.cancel()
                with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(pending,2)
            self.assertFalse(holder.done(),'cancelled waiter must not release another owner lock')
            self.assertEqual(len(list(self.archive.root.rglob('hist_*.json'))),1)
        finally:
            release.set();await holder
            if pending is not None:await asyncio.gather(pending,return_exceptions=True)
        self.archive.save('one',HumanMessage(content='healthy after cancelled wait'))

    async def test_actual_graph_request_reports_bounded_history_maintenance_warning(self):
        import test_context_runtime as fixture
        # Reuse the native fixture setup explicitly without importing its TestCase.
        fixture.RuntimeContextTests.setUp(self)
        await fixture.RuntimeContextTests.ask(self,allowed={'read_history_result'})
        archive=self.factory.history_archive
        archive.save('one',HumanMessage(content='valid'))
        directory=archive.root/archive._thread_key('one')
        (directory/('hist_'+'0'*64+'.json')).write_bytes(b'bad historical root')
        archive.collect_orphan_parts('one')
        await fixture.RuntimeContextTests.ask(self,text='继续',allowed={'read_history_result'})
        report=self.factory.context_reports['one']
        self.assertTrue(report['history_storage_warnings'])
        self.assertLessEqual(len(report['history_storage_warnings']),8)


class NativeLockCreationTests(unittest.TestCase):
    def test_first_lock_file_creation_is_safe_for_concurrent_native_callers(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from nailong.core.file_locks import file_lock
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            for iteration in range(20):
                gate=threading.Barrier(4)
                def enter(_):
                    gate.wait()
                    with file_lock(root/f'lock-{iteration}') as descriptor:
                        if os.name=='nt':return True
                        return os.fstat(descriptor).st_ino
                with ThreadPoolExecutor(max_workers=4) as pool:
                    values=list(pool.map(enter,range(4)))
                self.assertEqual(len(set(values)),1)
