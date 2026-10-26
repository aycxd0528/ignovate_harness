"""Real command outcomes and active output ownership survive long build cycles."""
import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

from test_managed_workflow import WorkflowFixture


class OutputProvenanceTests(WorkflowFixture):
    async def test_timed_out_process_does_not_adopt_partial_outputs(self):
        self.build("from pathlib import Path;import time;p=Path('bundle');p.mkdir(exist_ok=True);"
            "(p/'partial').write_text('unknown partial output');time.sleep(30)")
        self.steps[0]['timeout_seconds']=1
        self.configure(self.steps)
        first=await self.verify()
        self.assertNotEqual(first['status'],'passed')
        self.assertTrue(first['steps'][0]['timed_out'])
        head=json.loads((self.store.root/'output-authority-v1/head.json').read_bytes())
        self.assertEqual(head['generation'],0)
        self.steps[0]['command']=self.command("from pathlib import Path;(Path('bundle')/'partial').unlink()")
        self.configure(self.steps)
        with self.assertRaises(ValueError):await self.verify()
        self.assertEqual((self.root/'bundle/partial').read_text(),'unknown partial output')

    async def test_deep_unicode_project_path_supports_ownership_restart(self):
        from nailong.core.permissions import PermissionEngine
        from nailong.core.sessions import ProjectSessionStore
        from nailong.core.verification import VerificationService
        root=self.root.joinpath(*(['工程目录'*3]*20))
        (root/'.nailong').mkdir(parents=True)
        (root/'source.py').write_text('source')
        steps=[{'name':'build','kind':'build','command':self.command(
            "from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);(p/'product').write_text('output')"),
            'generated_paths':['bundle']}]
        (root/'.nailong/settings.json').write_text(json.dumps({'verification':{'steps':steps}}))
        store=ProjectSessionStore(root,base_dir=self.data.name)
        self.assertGreater(len(json.dumps({'version':1,'project':str(root)}).encode()),1024)
        for _ in range(2):
            service=VerificationService(root,ProjectSessionStore(root,base_dir=self.data.name))
            result=await service.run('deep',approval=lambda *args:'approve_once',
                permission_engine=PermissionEngine(root,rules={}))
            self.assertEqual(result['status'],'passed',result)
        self.assertEqual(json.loads((store.root/'output-authority-v1/head.json').read_bytes())['generation'],2)

    async def test_overlapping_file_then_directory_scope_retires_old_members(self):
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);"
            "(p/'retired.py').write_text('former output')",['bundle/retired.py'])
        self.assertEqual((await self.verify())['status'],'passed')
        self.steps[0].update(generated_paths=['bundle'],command=self.command(
            "from pathlib import Path;p=Path('bundle');(p/'retired.py').unlink(missing_ok=True);(p/'new').write_text('output')"))
        self.configure(self.steps)
        self.assertEqual((await self.verify())['status'],'passed')
        source=self.root/'bundle/retired.py';source.write_text('new user input')
        with self.assertRaises(ValueError):await self.verify()
        self.assertEqual(source.read_text(),'new user input')

    async def test_parent_metadata_update_preserves_other_output_scopes(self):
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);(p/'a').write_text('output a')",['bundle/a'])
        self.assertEqual((await self.verify())['status'],'passed')
        self.steps[0].update(generated_paths=['bundle/b'],command=self.command(
            "from pathlib import Path;(Path('bundle')/'b').write_text('output b')"))
        self.configure(self.steps)
        self.assertEqual((await self.verify())['status'],'passed')
        self.steps[0].update(generated_paths=['bundle/a'],command=self.command(
            "from pathlib import Path;(Path('bundle')/'a').write_text('rebuilt output a')"))
        self.configure(self.steps)
        self.assertEqual((await self.verify())['status'],'passed')
        self.assertEqual((self.root/'bundle/b').read_text(),'output b')

    async def test_real_process_crash_after_admission_does_not_adopt_unobserved_outputs(self):
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);(p/'product').write_text('unknown output')")
        code="""import asyncio,os,sys
from pathlib import Path
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
from nailong.core.permissions import PermissionEngine
import nailong.core.verification as verification
root=Path(sys.argv[1]);store=ProjectSessionStore(root,base_dir=sys.argv[2])
native=verification.execute_process
async def crash_after_command(*args,**kwargs):
    await native(*args,**kwargs)
    os._exit(74)
verification.execute_process=crash_after_command
asyncio.run(verification.VerificationService(root,store,task_store=TaskStore(store)).run('thread',
    approval=lambda *args:'approve_once',permission_engine=PermissionEngine(root,rules={})))
"""
        result=await asyncio.to_thread(subprocess.run,[sys.executable,'-B','-c',code,str(self.root),self.data.name],
            capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,74,result.stderr)
        head=json.loads((self.store.root/'output-authority-v1/head.json').read_bytes())
        self.assertEqual(head['generation'],0)
        with self.assertRaises(ValueError):await self.verify()
        self.assertEqual((self.root/'bundle/product').read_text(),'unknown output')

    async def test_more_than_100_declared_scopes_survive_event_truncation_and_restart(self):
        from nailong.core.sessions import ProjectSessionStore
        from nailong.core.verification import VerificationService
        paths=[f'bundle/{i:03}.txt' for i in range(140)]
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);"
            "[(p/f'{i:03}.txt').write_text('artifact') for i in range(140)]",paths)
        self.assertEqual((await self.verify())['status'],'passed')
        self.assertEqual(len(self.store.read_events('thread')[-1]['data']['declared_generated_paths']),100)
        store=ProjectSessionStore(self.root,base_dir=self.data.name)
        result=await VerificationService(self.root,store,self.goals,task_store=self.tasks).run('thread',
            approval=lambda *args:'approve_once',permission_engine=self.engine)
        self.assertEqual(result['status'],'passed')
        self.assertEqual(result['generated_count'],141)

    async def test_ownership_capacity_failure_does_not_publish_partial_claims(self):
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);"
            "[(p/f'{i:03}.txt').write_text('artifact') for i in range(30)]")
        with patch('nailong.core.output_authority.MAX_BYTES',50):
            with self.assertRaises(ValueError):await self.verify()
        head=json.loads((self.store.root/'output-authority-v1/head.json').read_bytes())
        self.assertEqual(head['generation'],0)
        self.assertFalse(self.goals.active().verification_succeeded)
        with self.assertRaises(ValueError):await self.verify()

    async def test_corrupt_live_membership_never_falls_back_to_old_claims(self):
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);(p/'product').write_text('artifact')")
        await self.verify()
        directory=self.store.root/'output-authority-v1'
        head=json.loads((directory/'head.json').read_bytes())
        metadata=json.loads((directory/(head['membership']+'.json')).read_bytes())
        part=directory/(metadata['parts'][0][0]+'.part')
        original=part.read_bytes();part.write_bytes(original[:-1])
        with self.assertRaises((OSError,ValueError)):await self.verify()
        self.assertEqual(part.read_bytes(),original[:-1])
        part.write_bytes(original)
        self.assertEqual((await self.verify())['status'],'passed')

    async def test_real_process_crash_after_completion_intent_recovers_without_event(self):
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);(p/'product').write_text('output')")
        code="""import asyncio,json,os,sys
from pathlib import Path
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
from nailong.core.permissions import PermissionEngine
from nailong.core.verification import VerificationService
import nailong.core.output_authority as authority
root=Path(sys.argv[1]);store=ProjectSessionStore(root,base_dir=sys.argv[2])
native=authority.atomic_write_bytes
def crash_head(path,data,**kwargs):
    if Path(path).name=='head.json' and json.loads(data).get('generation',0)>0:os._exit(73)
    return native(path,data,**kwargs)
authority.atomic_write_bytes=crash_head
asyncio.run(VerificationService(root,store,task_store=TaskStore(store)).run('thread',
    approval=lambda *args:'approve_once',permission_engine=PermissionEngine(root,rules={})))
"""
        result=await asyncio.to_thread(subprocess.run,[sys.executable,'-B','-c',code,str(self.root),self.data.name],
            capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,73,result.stderr)
        self.assertTrue((self.store.root/'output-authority-v1/intent.json').is_file())
        self.assertFalse(any(row['kind']=='verification' for row in self.store.read_events('thread')))
        self.assertEqual((await self.verify())['status'],'passed')

    async def test_repeated_cancellation_joins_native_head_publication_and_preserves_generation(self):
        import nailong.core.output_authority as authority
        from async_phases import wait_for_phase
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);(p/'product').write_text('output')")
        loop=asyncio.get_running_loop()
        started,release=asyncio.Event(),threading.Event()
        native=authority.atomic_write_bytes
        def hold_head(path,data,**kwargs):
            if Path(path).name=='head.json' and json.loads(data).get('generation',0)>0:
                loop.call_soon_threadsafe(started.set)
                if not release.wait(30):raise RuntimeError('test did not release native publication')
            return native(path,data,**kwargs)
        with patch.object(authority,'atomic_write_bytes',side_effect=hold_head):
            pending=asyncio.create_task(self.verify())
            try:
                await wait_for_phase(started,pending,description='native head publication entry')
                for _ in range(2):
                    pending.cancel()
                    await asyncio.sleep(.01)
                    self.assertFalse(pending.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):await pending
            finally:
                release.set()
                if not pending.done():pending.cancel()
                await asyncio.gather(pending,return_exceptions=True)
        self.assertEqual(self.store.read_events('thread')[-1]['data']['status'],'cancelled')
        self.assertFalse(self.goals.active().verification_succeeded)
        self.assertEqual((await self.verify())['status'],'passed')

    async def test_cross_process_ownership_lock_is_nonblocking_and_releases(self):
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);(p/'product').write_text('output')")
        ready,release=Path(self.data.name)/'ready',Path(self.data.name)/'release'
        code="""import sys,time
from pathlib import Path
from nailong.core.file_locks import file_lock
with file_lock(Path(sys.argv[1])/'output-authority.lock'):
    Path(sys.argv[2]).write_text('ready')
    while not Path(sys.argv[3]).exists():time.sleep(.01)
"""
        process=subprocess.Popen([sys.executable,'-B','-c',code,str(self.store.root),str(ready),str(release)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            async with asyncio.timeout(3):
                while not ready.exists():
                    if process.poll() is not None:self.fail(process.communicate()[1])
                    await asyncio.sleep(.005)
            with self.assertRaises(BlockingIOError):await asyncio.wait_for(self.verify(),1)
        finally:
            release.write_text('release')
            _,error=await asyncio.to_thread(process.communicate,timeout=5)
            self.assertEqual(process.returncode,0,error)
        self.assertEqual((await self.verify())['status'],'passed')

    async def test_legacy_retirement_is_replayed_by_stream_order_not_wall_clock(self):
        from nailong.core.sessions import ProjectSessionStore
        from nailong.core.verification import VerificationService
        from nailong.core.verification_artifacts import copy_manifest
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);c=p/'count';"
            "n=int(c.read_text()) if c.exists() else 0;c.write_text(str(n+1));"
            "(p/'current').write_text('artifact');old=p/'retired.py';"
            "old.write_text('old artifact') if n==0 else old.unlink(missing_ok=True)")
        first,second=await self.verify(),await self.verify()
        legacy_store=ProjectSessionStore(self.root,base_dir=Path(self.data.name)/'legacy')
        for stamp,record in (('2099-01-01T00:00:00+00:00',first),('2000-01-01T00:00:00+00:00',second)):
            legacy={k:v for k,v in record.items() if not k.startswith('output_authority_')}
            legacy['started_at']=stamp
            copy_manifest(self.store.root,record['artifact_manifest'],self.root,legacy_store.root)
            legacy_store.append_event('thread','verification',legacy)
        retired=self.root/'bundle/retired.py';retired.write_text('new user source')
        verifier=VerificationService(self.root,legacy_store)
        with self.assertRaises(ValueError):
            await verifier.run('thread',approval=lambda *args:'approve_once',permission_engine=self.engine)
        self.assertEqual(retired.read_text(),'new user source')

    async def test_legacy_conflicting_sessions_never_restore_retired_paths(self):
        from nailong.core.sessions import ProjectSessionStore
        from nailong.core.verification import VerificationService
        from nailong.core.verification_artifacts import copy_manifest
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);c=p/'count';"
            "n=int(c.read_text()) if c.exists() else 0;c.write_text(str(n+1));"
            "(p/'current').write_text('artifact');old=p/'retired.py';"
            "old.write_text('old artifact') if n==0 else old.unlink(missing_ok=True)")
        first,second=await self.verify(),await self.verify()
        retired=self.root/'bundle/retired.py';retired.write_text('new user source')
        for ordering in ((first,second),(second,first)):
            with self.subTest(order=[r['run_id'] for r in ordering]):
                legacy_store=ProjectSessionStore(self.root,base_dir=Path(self.data.name)/ordering[0]['run_id'])
                for thread,record in zip(('alpha','beta'),ordering):
                    copy_manifest(self.store.root,record['artifact_manifest'],self.root,legacy_store.root)
                    legacy_store.append_event(thread,'verification',
                        {k:v for k,v in record.items() if not k.startswith('output_authority_')})
                verifier=VerificationService(self.root,legacy_store)
                with self.assertRaises(ValueError):
                    await verifier.run('migrated',approval=lambda *args:'approve_once',permission_engine=self.engine)
                self.assertEqual(retired.read_text(),'new user source')
                self.assertEqual((self.root/'bundle/count').read_text(),'2')

    async def test_legacy_bad_old_manifest_does_not_block_independent_healthy_snapshot(self):
        from nailong.core.sessions import ProjectSessionStore
        from nailong.core.verification import VerificationService
        from nailong.core.verification_artifacts import copy_manifest
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);(p/'product').write_text('output')")
        first,second=await self.verify(),await self.verify()
        legacy_store=ProjectSessionStore(self.root,base_dir=Path(self.data.name)/'legacy')
        for record in (first,second):
            copy_manifest(self.store.root,record['artifact_manifest'],self.root,legacy_store.root)
            legacy_store.append_event('thread','verification',{k:v for k,v in record.items() if not k.startswith('output_authority_')})
        (legacy_store.root/'verification-artifacts-v1'/(first['artifact_manifest']['id']+'.json')).unlink()
        record=await VerificationService(self.root,legacy_store).run('thread',
            approval=lambda *args:'approve_once',permission_engine=self.engine)
        self.assertEqual(record['status'],'passed')
        self.assertTrue(record['artifact_authority_warnings'])

    async def test_missing_native_operation_key_does_not_grant_output_authority(self):
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);(p/'product').write_text('output')")
        await self.verify()
        directory=self.store.root/'output-authority-v1'
        keys=list(directory.glob('*.completion-key'))
        self.assertEqual(len(keys),1)
        keys[0].unlink()
        with self.assertRaises((OSError,ValueError)):await self.verify()

    async def test_unexecuted_step_outputs_do_not_hide_inputs_created_by_another_step(self):
        self.build("pass")
        self.steps=[{'name':'first','kind':'build','generated_paths':['build-a'],
            'command':self.command("from pathlib import Path;a=Path('build-a');a.mkdir();(a/'product').write_text('output');"
                "b=Path('build-b');b.mkdir();(b/'user-source.py').write_text('unregistered input')")},
            {'name':'second','kind':'build','generated_paths':['build-b'],
            'command':self.command("from pathlib import Path;(Path('build-b')/'user-source.py').unlink()") }]
        self.configure(self.steps)
        record=await self.verify()
        self.assertEqual(record['status'],'invalidated')
        self.assertFalse(record['steps'][1]['started'])
        self.assertEqual((self.root/'build-b/user-source.py').read_text(),'unregistered input')

    async def test_completed_generation_recovers_after_atomic_head_publication_failure(self):
        import nailong.core.output_authority as authority
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);(p/'product').write_text('output')")
        native=authority.atomic_write_bytes
        def fail_head(path,data,**kwargs):
            if Path(path).name=='head.json' and json.loads(data).get('generation',0)>0:
                raise OSError('head publication failed after durable completion intent')
            return native(path,data,**kwargs)
        with patch.object(authority,'atomic_write_bytes',side_effect=fail_head):
            with self.assertRaises(OSError):await self.verify()
        self.assertFalse(self.goals.active().verification_succeeded)
        self.assertEqual((await self.verify())['status'],'passed')

    async def test_missing_or_corrupt_head_never_reimports_historical_permissions(self):
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);(p/'product').write_text('output')")
        await self.verify()
        head=self.store.root/'output-authority-v1/head.json'
        original=head.read_bytes()
        for damaged in (b'',b'[]',b'{broken'):
            head.write_bytes(damaged)
            with self.assertRaises((ValueError,OSError)):await self.verify()
            self.assertEqual(head.read_bytes(),damaged)
        head.unlink()
        with self.assertRaises((ValueError,OSError)):await self.verify()
        self.assertFalse(head.exists())
        head.write_bytes(original)
        self.assertEqual((await self.verify())['status'],'passed')

    async def test_active_ownership_does_not_scan_or_trust_old_event_previews(self):
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);(p/'product').write_text('output')")
        await self.verify()
        with patch.object(self.store,'read_events',side_effect=AssertionError('historical display is not authority')):
            self.assertEqual((await self.verify())['status'],'passed')

    async def test_completed_generation_survives_failed_task_notification(self):
        goal=self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);(p/'product').write_text('output')")
        with patch.object(self.tasks,'record_verification',side_effect=OSError('task notification failed')):
            with self.assertRaises(OSError):await self.verify()
        self.assertFalse(self.goals.update(goal.id,state='complete',thread_id='thread')[0])
        self.assertEqual((await self.verify())['status'],'passed')

    async def test_completion_replay_is_idempotent_and_old_results_cannot_overwrite(self):
        from nailong.core.output_authority import OutputAuthority
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);(p/'product').write_text('output')")
        first=await self.verify()
        directory=self.store.root/'output-authority-v1'
        head=json.loads((directory/'head.json').read_bytes())
        operation=json.loads((directory/(head['operation']+'.json')).read_bytes())
        owner=OutputAuthority(self.store.root,self.root)
        before=(directory/'head.json').read_bytes()
        self.assertEqual(owner.complete(operation['admission'],first['artifact_manifest']),first['output_authority_token'])
        self.assertEqual((directory/'head.json').read_bytes(),before)
        await self.verify()
        newer=(directory/'head.json').read_bytes()
        with self.assertRaises(ValueError):owner.complete(operation['admission'],first['artifact_manifest'])
        self.assertEqual((directory/'head.json').read_bytes(),newer)

    async def test_cancellation_before_complete_output_observation_does_not_adopt_files(self):
        self.build("from pathlib import Path;import time;p=Path('bundle');p.mkdir(exist_ok=True);"
            "(p/'started').write_text('unknown partial output');time.sleep(30)")
        pending=asyncio.create_task(self.verify())
        try:
            await self.wait_file('bundle/started',pending)
            pending.cancel();pending.cancel()
            with self.assertRaises(asyncio.CancelledError):await pending
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending,return_exceptions=True)
        self.assertEqual(self.store.read_events('thread')[-1]['data']['status'],'cancelled')
        self.steps[0]['command']=self.command("from pathlib import Path;(Path('bundle')/'started').unlink()")
        self.configure(self.steps)
        with self.assertRaises(ValueError):await self.verify()
        self.assertEqual((self.root/'bundle/started').read_text(),'unknown partial output')

    def build(self, body, paths=('bundle',)):
        self.steps = [{'name':'build','kind':'build','command':self.command(body),
            'generated_paths':list(paths)}]
        self.configure(self.steps)
        self.begin(scope=['source.py'])
        self.tasks.add_acceptance('thread','artifact','deliver built output',kind='build')
        self.tasks.bind_verification('thread','artifact','build',['source.py'])
        return self.goals.create('修复功能',thread_id='thread')

    async def test_first_failed_build_can_retry_without_claiming_success(self):
        goal = self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);"
            "f=p/'ready';was=f.exists();f.write_text('owned output');raise SystemExit(0 if was else 1)")
        first = await self.verify()
        self.assertEqual(first['status'],'failed')
        self.assertFalse(self.goals.update(goal.id,state='complete',thread_id='thread')[0])
        self.assertNotEqual(self.tasks.snapshot('thread')['lifecycle'],'completed')
        second = await self.verify()
        self.assertEqual(second['status'],'passed')
        self.assertTrue(self.goals.update(goal.id,state='complete',thread_id='thread')[0])

    async def test_retired_output_cannot_authorize_new_user_source_at_same_path(self):
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);c=p/'count';"
            "n=int(c.read_text()) if c.exists() else 0;c.write_text(str(n+1));"
            "(p/'current').write_text('artifact');old=p/'retired.py';"
            "old.write_text('old artifact') if n==0 else old.unlink(missing_ok=True)")
        self.assertEqual((await self.verify())['status'],'passed')
        second = await self.verify()
        self.assertEqual(second['status'],'passed')
        self.assertNotIn('bundle/retired.py',second['generated_paths'])
        retired = self.root/'bundle/retired.py'
        retired.write_text('new user source')
        with self.assertRaises(ValueError):
            await self.verify()
        self.assertEqual(retired.read_text(),'new user source')
        self.assertEqual((self.root/'bundle/count').read_text(),'2')

    async def test_generation_survives_failure_to_append_verification_event(self):
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);(p/'product').write_text('output')")
        append = self.store.append_event
        def fail_event(thread, kind, data, *args, **kwargs):
            if kind == 'verification':
                raise OSError('native event publication failure')
            return append(thread,kind,data,*args,**kwargs)
        with patch.object(self.store,'append_event',side_effect=fail_event):
            with self.assertRaises(OSError):
                await self.verify()
        from nailong.core.sessions import ProjectSessionStore
        from nailong.core.verification import VerificationService
        reopened = ProjectSessionStore(self.root,base_dir=self.data.name)
        verifier = VerificationService(self.root,reopened,self.goals,task_store=self.tasks)
        second = await verifier.run('thread',approval=lambda *args:'approve_once',permission_engine=self.engine)
        self.assertEqual(second['status'],'passed')

    async def test_failed_build_output_ownership_is_shared_across_sessions(self):
        self.build("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);"
            "f=p/'ready';was=f.exists();f.write_text('owned output');raise SystemExit(0 if was else 1)")
        self.assertEqual((await self.verify())['status'],'failed')
        self.tasks.begin('other','repair build',scope=['source.py'])
        from nailong.core.verification import VerificationService
        record = await VerificationService(self.root,self.store,task_store=self.tasks).run('other',
            approval=lambda *args:'approve_once',permission_engine=self.engine)
        self.assertEqual(record['status'],'passed')
