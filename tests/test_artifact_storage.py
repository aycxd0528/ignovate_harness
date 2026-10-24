"""Actual failed observations and incomplete copies must not grow orphan storage."""
import json
import asyncio
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_managed_workflow import WorkflowFixture


class ArtifactStorageTests(WorkflowFixture):
    def storage(self):
        self.assertIsNotNone(importlib.util.find_spec('nailong.core.artifact_storage'),
            '缺少原生产物发布恢复入口')
        from nailong.core import artifact_storage
        return artifact_storage

    def build(self):
        self.configure([{'name':'build','kind':'build','command':self.command(
            "from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);"
            "[(p/f'{i:04}.txt').write_text('artifact') for i in range(101)]"),
            'generated_paths':['bundle']}])
        self.begin(scope=['source.py'])

    def parts(self, boundary):
        return set((Path(boundary)/'verification-artifacts-v1').glob('*.part'))

    async def test_native_capacity_failure_does_not_publish_partial_parts(self):
        self.build()
        with patch('nailong.core.verification_artifacts.CHUNK_BYTES',1024),\
                patch('nailong.core.verification_artifacts.MAX_BYTES',5000):
            with self.assertRaises(ValueError):await self.verify()
        self.assertEqual(self.parts(self.store.root),set())
        self.assertFalse(self.tasks.snapshot('thread').get('verification_manifest'))

    async def test_failed_header_publication_rolls_back_only_uncommitted_parts(self):
        import nailong.core.verification_artifacts as artifacts
        self.build()
        native=artifacts._publish
        def fail_header(boundary,data,suffix):
            if suffix=='.json' and json.loads(data).get('run_id')!='preflight':
                raise OSError('native header publication failure')
            return native(boundary,data,suffix)
        with patch.object(artifacts,'CHUNK_BYTES',1024),patch.object(artifacts,'_publish',side_effect=fail_header):
            with self.assertRaises(OSError):await self.verify()
        self.assertEqual(self.parts(self.store.root),set())
        self.assertFalse(self.tasks.snapshot('thread').get('verification_manifest'))

    async def test_copy_validates_last_part_before_publishing_any_destination_part(self):
        from nailong.core.verification_artifacts import copy_manifest
        self.build()
        with patch('nailong.core.verification_artifacts.CHUNK_BYTES',1024):
            record=await self.verify()
        directory=self.store.root/'verification-artifacts-v1'
        header=json.loads((directory/(record['artifact_manifest']['id']+'.json')).read_bytes())
        last=directory/(header['parts'][-1][0]+'.part')
        last.write_bytes(last.read_bytes()[:-1])
        destination=Path(self.data.name).resolve()/'copy'
        with self.assertRaises(ValueError):copy_manifest(self.store.root,record['artifact_manifest'],self.root,destination)
        self.assertEqual(self.parts(destination),set())

    async def test_orphan_collection_keeps_all_complete_history_and_unknown_files(self):
        from nailong.core.verification import current_evidence
        from nailong.core.verification_artifacts import _publish
        self.build();record=await self.verify();before=self.parts(self.store.root)
        identity=_publish(self.store.root,b'legacy unreferenced fragment','.part')
        directory=self.store.root/'verification-artifacts-v1'
        unknown=directory/'user-note.txt';unknown.write_text('keep me')
        result=self.storage().collect_orphan_parts(self.store.root)
        self.assertEqual(result['removed_parts'],1)
        self.assertFalse((directory/(identity+'.part')).exists())
        self.assertEqual(self.parts(self.store.root),before)
        self.assertEqual(unknown.read_text(),'keep me')
        self.assertEqual(current_evidence(self.root,record,self.store.root),record['input_after'])
        self.assertEqual(self.storage().collect_orphan_parts(self.store.root)['removed_parts'],0)

    async def test_bad_historical_header_keeps_parts_and_reports_bounded_diagnostic(self):
        from nailong.core.verification_artifacts import _publish
        self.build();await self.verify()
        directory=self.store.root/'verification-artifacts-v1'
        (directory/('0'*64+'.json')).write_text('damaged old header')
        identity=_publish(self.store.root,b'unknown unreferenced fragment','.part')
        result=self.storage().collect_orphan_parts(self.store.root)
        self.assertEqual(result['removed_parts'],0)
        self.assertTrue(result['warnings'])
        self.assertTrue((directory/(identity+'.part')).exists())
        record=await self.verify()
        self.assertEqual(record['status'],'passed')
        self.assertTrue(record['artifact_storage_warnings'])
        self.assertLessEqual(len(record['artifact_storage_warnings']),8)

    async def test_failed_publication_preserves_repaired_part_referenced_by_old_header(self):
        from nailong.core.verification import current_evidence
        from nailong.core.verification_artifacts import _publish,_json
        self.build();record=await self.verify()
        directory=self.store.root/'verification-artifacts-v1'
        header=json.loads((directory/(record['artifact_manifest']['id']+'.json')).read_bytes())
        with tempfile.TemporaryFile() as spool:
            staged=[]
            for identity,size in header['parts']:
                data=(directory/(identity+'.part')).read_bytes()
                staged.append((spool.tell(),identity,size));spool.write(data)
            restored=directory/(header['parts'][0][0]+'.part');restored.unlink()
            header['run_id']='new failed publication'
            def fail_header(boundary,data,suffix):
                if suffix=='.json':raise OSError('native header failure after repairing shared part')
                return _publish(boundary,data,suffix)
            with self.assertRaises(OSError):
                self.storage().publish_manifest(self.store.root,_json(header),spool,staged,fail_header)
        self.assertTrue(restored.exists())
        self.assertEqual(current_evidence(self.root,record,self.store.root),record['input_after'])

    async def crash_publication(self, stage):
        self.build()
        code="""import asyncio,json,os,sys
from pathlib import Path
from nailong.core.sessions import ProjectSessionStore
from nailong.core.permissions import PermissionEngine
from nailong.core.verification import VerificationService
import nailong.core.verification_artifacts as artifacts
root=Path(sys.argv[1]);store=ProjectSessionStore(root,base_dir=sys.argv[2]);stage=sys.argv[3]
native=artifacts._publish
def crash(boundary,data,suffix):
    result=native(boundary,data,suffix)
    if stage=='part' and suffix=='.part' or stage=='head' and suffix=='.json' and json.loads(data).get('run_id')!='preflight':
        os._exit(76)
    return result
artifacts._publish=crash
asyncio.run(VerificationService(root,store).run('thread',approval=lambda *a:'approve_once',permission_engine=PermissionEngine(root,rules={})))
"""
        result=await asyncio.to_thread(subprocess.run,[sys.executable,'-B','-c',code,str(self.root),self.data.name,stage],
            capture_output=True,text=True,timeout=12)
        self.assertEqual(result.returncode,76,result.stderr)
        self.assertTrue(self.parts(self.store.root))

    async def test_real_exit_after_partial_publication_recovers_without_replaying_command(self):
        await self.crash_publication('part')
        result=self.storage().collect_orphan_parts(self.store.root)
        self.assertGreater(result['removed_parts'],0)
        self.assertEqual(self.parts(self.store.root),set())
        self.assertEqual(len(list((self.root/'bundle').iterdir())),101)
        with self.assertRaises(ValueError):await self.verify()

    async def test_real_exit_after_complete_header_preserves_proof_during_recovery(self):
        from nailong.core.verification_artifacts import artifact_index,load_manifest
        await self.crash_publication('head')
        before=self.parts(self.store.root)
        result=self.storage().collect_orphan_parts(self.store.root)
        self.assertEqual(result['removed_parts'],0)
        self.assertEqual(self.parts(self.store.root),before)
        for path in (self.store.root/'verification-artifacts-v1').glob('*.json'):
            value=json.loads(path.read_bytes())
            if value.get('run_id') and value['run_id']!='preflight':
                with artifact_index() as index:
                    header=load_manifest(self.store.root,{'version':1,'id':path.stem},self.root,index)
                    self.assertEqual(header['count'],102)
                break
        else:self.fail('完整头未发布')

    async def test_corrupt_intent_is_preserved_and_never_resets_or_deletes(self):
        self.build();await self.verify()
        directory=self.store.root/'verification-artifacts-v1'
        intent=directory/'publication.json';intent.write_text('broken intent')
        before=self.parts(self.store.root)
        with self.assertRaises(ValueError):self.storage().collect_orphan_parts(self.store.root)
        self.assertEqual(intent.read_text(),'broken intent')
        self.assertEqual(self.parts(self.store.root),before)

    async def test_graph_capacity_failure_never_uses_partial_reference_set_to_delete(self):
        from nailong.core.verification_artifacts import _publish
        self.build();await self.verify()
        identity=_publish(self.store.root,b'orphan retained when graph unknown','.part')
        before=self.parts(self.store.root)
        with patch.object(self.storage(),'MAX_GRAPH_BYTES',1):
            result=self.storage().collect_orphan_parts(self.store.root)
        self.assertEqual(result['removed_parts'],0)
        self.assertTrue(result['warnings'])
        self.assertEqual(self.parts(self.store.root),before)
        self.assertTrue(any(path.stem==identity for path in before))

    @unittest.skipIf(os.name=='nt','POSIX symlink; native Windows reparse tests require Windows')
    async def test_collection_does_not_follow_linked_fragment_or_delete_external_data(self):
        self.build();await self.verify()
        outside=Path(self.data.name).resolve()/'external';outside.write_text('external user data')
        directory=self.store.root/'verification-artifacts-v1'
        link=directory/('a'*64+'.part');link.symlink_to(outside)
        before=self.parts(self.store.root)
        result=self.storage().collect_orphan_parts(self.store.root)
        self.assertEqual(result['removed_parts'],0)
        self.assertTrue(result['warnings'])
        self.assertTrue(link.is_symlink())
        self.assertEqual(outside.read_text(),'external user data')
        self.assertEqual(self.parts(self.store.root),before)

    async def test_repeated_cancel_while_waiting_for_real_publication_lock_closes_spool(self):
        from nailong.core.file_locks import file_lock
        import nailong.core.verification_artifacts as artifacts
        self.build();storage=self.storage();ready=threading.Event();opened=[]
        native_lock=storage.file_lock;native_temporary=artifacts.tempfile.TemporaryFile
        def observe_lock(*args,**kwargs):
            ready.set()
            return native_lock(*args,**kwargs)
        def temporary(*args,**kwargs):
            stream=native_temporary(*args,**kwargs);opened.append(stream);return stream
        with file_lock(self.store.root/'artifact-publication.lock'),\
                patch.object(storage,'file_lock',side_effect=observe_lock),\
                patch.object(artifacts.tempfile,'TemporaryFile',side_effect=temporary):
            pending=asyncio.create_task(self.verify())
            try:
                async with asyncio.timeout(3):
                    while not ready.is_set():
                        if pending.done():await pending
                        await asyncio.sleep(.005)
                pending.cancel();pending.cancel()
                with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(pending,2)
            finally:
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending,return_exceptions=True)
        self.assertTrue(opened)
        self.assertTrue(all(stream.closed for stream in opened))
        self.assertEqual((await self.verify())['status'],'passed')

    async def test_opposite_direction_native_copies_finish_without_lock_order_deadlock(self):
        from nailong.core.verification_artifacts import copy_manifest,artifact_index,load_manifest
        self.build();record=await self.verify();other=Path(self.data.name).resolve()/'other'
        reference=record['artifact_manifest'];copy_manifest(self.store.root,reference,self.root,other)
        code="""import sys
from nailong.core.verification_artifacts import copy_manifest
copy_manifest(sys.argv[1],{'version':1,'id':sys.argv[4]},sys.argv[3],sys.argv[2])
"""
        processes=[]
        try:
            for source,destination in ((self.store.root,other),(other,self.store.root)):
                processes.append(subprocess.Popen([sys.executable,'-B','-c',code,str(source),str(destination),
                    str(self.root),reference['id']],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
            results=await asyncio.gather(*(asyncio.to_thread(process.communicate,timeout=10) for process in processes))
            for process,(_,error) in zip(processes,results):self.assertEqual(process.returncode,0,error)
        finally:
            for process in processes:
                if process.poll() is None:process.kill();process.communicate(timeout=3)
        for boundary in (self.store.root,other):
            with artifact_index() as index:
                self.assertEqual(load_manifest(boundary,reference,self.root,index)['count'],102)

    async def test_complete_header_cleanup_failure_does_not_lose_completed_observation(self):
        self.build();storage=self.storage();native=storage._unlink;failed=False
        def fail_committed_intent(directory,name):
            nonlocal failed
            if not failed and name=='publication.json' and directory==self.store.root/'verification-artifacts-v1':
                intent=json.loads((directory/name).read_bytes())
                header=json.loads((directory/(intent['header']+'.json')).read_bytes())
                if header['run_id']!='preflight':
                    failed=True
                    raise OSError('native intent cleanup failure after durable header')
            return native(directory,name)
        with patch.object(storage,'_unlink',side_effect=fail_committed_intent):
            record=await self.verify()
        self.assertTrue(failed)
        self.assertEqual(record['status'],'passed')
        self.assertTrue(record['artifact_storage_warnings'])
        before=self.parts(self.store.root)
        self.assertEqual(storage.collect_orphan_parts(self.store.root)['removed_parts'],0)
        self.assertEqual(self.parts(self.store.root),before)
        self.assertEqual((await self.verify())['status'],'passed')

    async def test_failed_recovery_persists_diagnostic_about_retained_orphans(self):
        from nailong.core.verification_artifacts import _publish
        self.build();await self.verify()
        directory=self.store.root/'verification-artifacts-v1'
        (directory/('0'*64+'.json')).write_text('unreadable superseded header')
        steps=[{'name':'build','kind':'build','command':self.command(
            "from pathlib import Path;p=Path('bundle');[(p/f'{i:04}.txt').write_text('new output') for i in range(101)]"),
            'generated_paths':['bundle']}]
        self.configure(steps)
        def fail_header(boundary,data,suffix):
            if suffix=='.json' and json.loads(data).get('run_id')!='preflight':raise OSError('native header failure')
            return _publish(boundary,data,suffix)
        with patch('nailong.core.verification_artifacts._publish',side_effect=fail_header):
            with self.assertRaises(OSError):await self.verify()
        maintenance=json.loads((directory/'maintenance.json').read_bytes())
        self.assertTrue(maintenance['warnings'])
        record=await self.verify()
        self.assertEqual(record['status'],'passed')
        self.assertTrue(record['artifact_storage_warnings'])

    @unittest.skipIf(os.name=='nt','POSIX directory rename; native Windows pin denies replacement')
    async def test_directory_swap_during_collection_never_deletes_replacement_referenced_part(self):
        self.build();record=await self.verify();storage=self.storage()
        directory=self.store.root/'verification-artifacts-v1';replacement=self.store.root/'replacement'
        header=json.loads((directory/(record['artifact_manifest']['id']+'.json')).read_bytes())
        name=header['parts'][0][0]+'.part';data=(directory/name).read_bytes()
        directory.rename(replacement);directory.mkdir();(directory/name).write_bytes(data)
        native=storage.os.scandir;calls=0;abandoned=self.store.root/'abandoned'
        def swap_before_sweep(path):
            nonlocal calls
            calls+=1
            if calls==2:
                directory.rename(abandoned);replacement.rename(directory)
            return native(path)
        with patch.object(storage.os,'scandir',side_effect=swap_before_sweep):
            with self.assertRaises(ValueError):storage.collect_orphan_parts(self.store.root)
        self.assertEqual((directory/name).read_bytes(),data)
        self.assertTrue((directory/(record['artifact_manifest']['id']+'.json')).exists())

    @unittest.skipIf(os.name=='nt','POSIX directory rename; native Windows pin denies replacement')
    async def test_directory_swap_during_publication_never_mutates_replacement_store(self):
        import nailong.core.verification_artifacts as artifacts
        self.build();record=await self.verify();reference=record['artifact_manifest']
        target=Path(self.data.name).resolve()/'copy';artifacts.copy_manifest(self.store.root,reference,self.root,target)
        directory=target/'verification-artifacts-v1';replacement=target/'replacement'
        before={p.name:p.read_bytes() for p in directory.iterdir()}
        directory.rename(replacement);directory.mkdir()
        native=artifacts._publish;swapped=False
        def swap_after_part(boundary,data,suffix):
            nonlocal swapped
            identity=native(boundary,data,suffix)
            if boundary==target and suffix=='.part' and not swapped:
                swapped=True;directory.rename(target/'abandoned');replacement.rename(directory)
            return identity
        with patch.object(artifacts,'_publish',side_effect=swap_after_part):
            with self.assertRaises(ValueError):artifacts.copy_manifest(self.store.root,reference,self.root,target)
        self.assertTrue(swapped)
        self.assertEqual({p.name:p.read_bytes() for p in directory.iterdir()},before)
