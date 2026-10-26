"""Slow completion observations leave goal control available and cannot rebind."""
import asyncio
from contextlib import contextmanager
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

import test_managed_workflow as workflow
import test_adaptive_coordination as coordination
from nailong.core.verification import input_fingerprint,VerificationService
from platform_fixtures import python_command
import local_tools


class PausedCompletionFingerprint:
    def __init__(self,release_timeout=2):
        self.entered=threading.Event();self.release=threading.Event()
        self.release_timeout=release_timeout
        self._lock=threading.Lock();self.calls=0

    def __call__(self,root,generated=()):
        value=input_fingerprint(root,generated)
        with self._lock:
            self.calls+=1;first=self.calls==1
        if first:
            self.entered.set()
            if not self.release.wait(self.release_timeout):raise AssertionError('completion scan was not released')
        return value


async def start_completion(test,goals,goal_id,thread,gate):
    pending=asyncio.create_task(asyncio.to_thread(goals.update,goal_id,state='complete',thread_id=thread))
    async def cleanup():
        gate.release.set();await asyncio.gather(pending,return_exceptions=True)
    test.addAsyncCleanup(cleanup)
    test.assertTrue(await asyncio.to_thread(gate.entered.wait,1))
    return pending


class GoalCompletionObservationTests(unittest.IsolatedAsyncioTestCase):
    setUp=workflow.WorkflowFixture.setUp
    command=workflow.WorkflowFixture.command
    configure=workflow.WorkflowFixture.configure
    begin=workflow.WorkflowFixture.begin
    verification=workflow.WorkflowFixture.verification
    verify=workflow.WorkflowFixture.verify

    async def asyncSetUp(self):
        self.begin(scope=['source.py'])
        self.tasks.add_acceptance('thread','feature','功能正确',kind='test')
        self.tasks.bind_verification('thread','feature','test',['source.py'])
        self.goal=self.goals.create('修复功能',thread_id='thread')
        self.record=await self.verify()
        self.assertEqual(self.record['status'],'passed')

    async def test_goal_reads_and_user_pause_do_not_wait_for_completion_scan(self):
        gate=PausedCompletionFingerprint(release_timeout=30)
        with patch('nailong.core.verification.input_fingerprint',gate):
            pending=await start_completion(self,self.goals,self.goal.id,'thread',gate)
            # A lock regression is released eventually, but cannot pass by
            # waiting for that release. File I/O latency is not the contract.
            watchdog=threading.Timer(15,gate.release.set);watchdog.start()
            try:
                current=self.goals.active()
                paused,reason,_=self.goals.update(self.goal.id,state='paused',summary='user paused',thread_id='thread')
                self.assertFalse(gate.release.is_set(),'goal control waited for completion scan release')
                self.assertFalse(pending.done(),'completion observation must remain pending while held')
                self.assertEqual(current.id,self.goal.id);self.assertTrue(paused,reason)
            finally:
                watchdog.cancel();watchdog.join();gate.release.set()
                await asyncio.gather(pending,return_exceptions=True)
            completed,_,_=await pending
        self.assertFalse(completed)
        self.assertEqual(self.goals.get(self.goal.id).pause_reason,'user paused')
        self.assertNotEqual(self.tasks.snapshot('thread')['lifecycle'],'completed')

    async def test_same_revision_task_pause_is_not_overwritten_by_old_observation(self):
        gate=PausedCompletionFingerprint()
        with patch('nailong.core.verification.input_fingerprint',gate):
            pending=await start_completion(self,self.goals,self.goal.id,'thread',gate)
            before=self.tasks.snapshot('thread')
            changed=self.tasks.set_state('thread',lifecycle='paused')
            self.assertEqual(before['revision'],changed['revision'])
            gate.release.set();completed,_,_=await pending
        self.assertFalse(completed)
        self.assertEqual(self.tasks.snapshot('thread')['lifecycle'],'paused')

    async def test_already_paused_task_is_not_completed_without_resuming(self):
        self.tasks.set_state('thread',lifecycle='paused')
        completed,_,_=await asyncio.to_thread(self.goals.update,self.goal.id,
            state='complete',thread_id='thread')
        self.assertFalse(completed)
        self.assertEqual(self.tasks.snapshot('thread')['lifecycle'],'paused')

    async def test_native_tool_repeated_cancellation_stops_after_pending_input_read(self):
        from tools import build_tools
        from nailong.tools.execution import ToolExecutionContext
        from nailong.tools.files import FileSession
        with (self.root/'source.py').open('wb') as stream:stream.truncate(8*1024*1024)
        self.assertEqual((await self.verify())['status'],'passed')
        execution=ToolExecutionContext(self.root,approval_handler=lambda *args:'approve_once')
        tool=next(tool for tool in build_tools(file_session=FileSession(self.root),
            execution_context=execution,goal_store=self.goals,thread_id='thread') if tool.name=='update_goal')
        entered,release,closed=threading.Event(),threading.Event(),threading.Event()
        reads=[];original=local_tools.open_regular_file
        @contextmanager
        def pause_read(path,*args,**kwargs):
            with original(path,*args,**kwargs) as stream:
                if Path(path).name!='source.py':
                    yield stream;return
                class Reader:
                    def fileno(self):return stream.fileno()
                    def read(self,size):
                        reads.append(size)
                        if len(reads)==1:
                            entered.set()
                            if not release.wait(3):raise AssertionError('input read was not released')
                        return stream.read(size)
                try:yield Reader()
                finally:closed.set()
        with patch('local_tools.open_regular_file',pause_read):
            pending=asyncio.create_task(tool.ainvoke({'type':'tool_call','name':'update_goal','id':'finish',
                'args':{'state':'complete'}},config={'configurable':{'thread_id':'thread'}}))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,2))
                pending.cancel();await asyncio.sleep(0);pending.cancel();release.set()
                with self.assertRaises(asyncio.CancelledError):await pending
            finally:
                release.set();await asyncio.gather(pending,return_exceptions=True)
        self.assertTrue(closed.is_set())
        self.assertEqual(len(reads),1,'a cancelled completion must not keep scanning inputs')
        self.assertEqual(self.goals.get(self.goal.id).state,'active')

    async def test_requirement_revision_changed_during_scan_remains_unfinished(self):
        gate=PausedCompletionFingerprint()
        with patch('nailong.core.verification.input_fingerprint',gate):
            pending=await start_completion(self,self.goals,self.goal.id,'thread',gate)
            changed=self.tasks.amend('thread',constraints=['additional requirement'])
            gate.release.set();completed,_,_=await pending
        self.assertFalse(completed)
        current=self.tasks.snapshot('thread')
        self.assertEqual(current['revision'],changed['revision'])
        self.assertNotEqual(current['lifecycle'],'completed')

    async def test_new_native_verification_during_scan_is_preserved_and_old_scan_cannot_commit(self):
        gate=PausedCompletionFingerprint()
        with patch('nailong.core.verification.input_fingerprint',gate):
            pending=await start_completion(self,self.goals,self.goal.id,'thread',gate)
            watchdog=threading.Timer(.5,gate.release.set);watchdog.start()
            try:new=await self.verify()
            finally:
                watchdog.cancel();watchdog.join();gate.release.set()
            completed,_,_=await pending
        self.assertEqual(new['status'],'passed')
        self.assertFalse(completed)
        self.assertEqual(self.goals.get(self.goal.id).verification_run_id,new['run_id'])

    async def test_old_verification_receipt_cannot_clear_new_revision_proof(self):
        newer=[]
        async def revise_and_verify(event):
            self.tasks.amend('thread',constraints=['new user requirement'])
            newer.append(await self.verify())
            self.assertEqual(self.goals.get(self.goal.id).verification_run_id,newer[0]['run_id'])
        old=await self.verification().run('thread',approval=lambda *args:'approve_once',
            emit=revise_and_verify,permission_engine=self.engine)
        self.assertEqual(old['status'],'passed');self.assertEqual(newer[0]['status'],'passed')
        self.assertLess(old['task_revision'],newer[0]['task_revision'])
        self.assertEqual(self.goals.get(self.goal.id).verification_run_id,newer[0]['run_id'])
        self.assertTrue(self.goals.get(self.goal.id).verification_succeeded)

    async def test_unchanged_native_verification_can_complete_both_task_and_goal(self):
        completed,reason,saved=await asyncio.to_thread(self.goals.update,self.goal.id,
            state='complete',thread_id='thread')
        self.assertTrue(completed,reason);self.assertEqual(saved.state,'complete')
        self.assertEqual(self.tasks.snapshot('thread')['lifecycle'],'completed')


class CoordinationCompletionObservationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=coordination.CoordinationTests.asyncSetUp
    asyncTearDown=coordination.CoordinationTests.asyncTearDown
    coordinator=coordination.CoordinationTests.coordinator

    async def prepare_owner_verification(self):
        tasks=self.factory.task_store;goals=self.factory.goal_store
        steps=[{'name':'check','kind':'test','command':python_command('pass')}]
        tasks.begin('owner',self.goal.objective)
        tasks.configure_verification('owner',steps)
        tasks.add_acceptance('owner','feature','目标覆盖',kind='test')
        tasks.bind_verification('owner','feature','check',['.'])
        record=await VerificationService(self.root,self.factory.session_store,goals,
            task_store=tasks,steps_override=steps).run('owner',approval=lambda *args:'approve_once')
        self.assertEqual(record['status'],'passed')
        return tasks,goals

    async def test_new_native_dispatch_during_completion_scan_blocks_overall_completion(self):
        tasks,goals=await self.prepare_owner_verification()
        gate=PausedCompletionFingerprint()
        with patch('nailong.core.verification.input_fingerprint',gate):
            pending=await start_completion(self,goals,self.goal.id,'owner',gate)
            watchdog=threading.Timer(.5,gate.release.set);watchdog.start()
            try:
                c=self.coordinator();task=tasks.snapshot('target')
                lease=c.acquire('target',task['revision'],['.'])
                self.assertTrue(lease['ok'],lease)
                admitted=c.dispatch('late','target','核对 target',lease['generation'],[])
                self.assertTrue(admitted['ok'],admitted)
            finally:
                watchdog.cancel();watchdog.join();gate.release.set()
            completed,reason,_=await pending
        self.assertFalse(completed,reason)
        self.assertEqual(c.status('late')['dispatches'][0]['state'],'queued')
        self.assertNotEqual(tasks.snapshot('owner')['lifecycle'],'completed')

    async def test_dispatch_cannot_publish_an_old_active_snapshot_after_goal_completes(self):
        tasks,goals=await self.prepare_owner_verification()
        c=self.coordinator();target=tasks.snapshot('target')
        lease=c.acquire('target',target['revision'],['.']);self.assertTrue(lease['ok'],lease)
        entered=threading.Event();release=threading.Event();original=c._goal
        def observed_goal():
            goal=original();entered.set()
            if not release.wait(2):raise AssertionError('dispatch snapshot was not released')
            return goal
        with patch.object(c,'_goal',side_effect=observed_goal):
            pending=asyncio.create_task(asyncio.to_thread(c.dispatch,'late','target','核对 target',lease['generation'],[]))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,1))
                completed,reason,_=await asyncio.to_thread(goals.update,self.goal.id,state='complete',thread_id='owner')
                self.assertTrue(completed,reason)
            finally:release.set()
            admitted=await pending
        self.assertFalse(admitted['ok'],admitted)
        self.assertEqual(c.status('late')['dispatches'],[])

    async def test_dispatch_cannot_reuse_authorization_after_same_goal_is_revised_and_resumed(self):
        goals=self.factory.goal_store;c=self.coordinator();target=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',target['revision'],['.']);self.assertTrue(lease['ok'],lease)
        entered,release=threading.Event(),threading.Event();original=c._goal
        def observed_goal():
            goal=original();entered.set()
            if not release.wait(2):raise AssertionError('dispatch snapshot was not released')
            return goal
        with patch.object(c,'_goal',side_effect=observed_goal):
            pending=asyncio.create_task(asyncio.to_thread(c.dispatch,'late','target','核对 target',lease['generation'],[]))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,1))
                self.assertTrue(goals.update(self.goal.id,state='paused',thread_id='owner')[0])
                goals.revise(self.goal.id,objective='仅讨论当前文件，禁止接管任何会话',thread_id='owner')
                self.assertTrue(goals.resume(self.goal.id,thread_id='owner')[0])
            finally:release.set()
            admitted=await pending
        with self.assertRaises(ValueError):original()
        self.assertFalse(admitted['ok'],admitted)
        self.assertEqual(c.status('late')['dispatches'],[])


class GoalTaskLockOrderTests(unittest.TestCase):
    def test_task_owner_and_goal_completion_or_verification_do_not_deadlock(self):
        script='''import asyncio,json,os,sys,threading
from pathlib import Path
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
from nailong.core.goal import GoalStore
from nailong.core.verification import VerificationService
from platform_fixtures import python_command
root=Path(sys.argv[1]);project=root/'project';project.mkdir()
sessions=ProjectSessionStore(project,base_dir=root/'data');tasks=TaskStore(sessions)
steps=[{'name':'check','kind':'test','command':python_command('pass')}]
tasks.begin('thread','work');tasks.configure_verification('thread',steps)
goals=GoalStore(sessions.root/'goals.json',project_root=project);goals.task_store=tasks
goal=goals.create('work',thread_id='thread')
record=asyncio.run(VerificationService(project,sessions,goals,task_store=tasks,steps_override=steps).run(
    'thread',approval=lambda *args:'approve_once'))
assert record['status']=='passed',record
task=tasks.snapshot('thread');held=threading.Event();entered=threading.Event();errors=[]
original=tasks.snapshot
def snapshot(*args,**kwargs):
    if threading.current_thread().name=='completion':entered.set()
    return original(*args,**kwargs)
tasks.snapshot=snapshot
def owner():
    try:
        with tasks.bound_task('thread',task['task_id'],task['revision']):
            held.set();entered.wait(.3);goals.get(goal.id)
    except BaseException as error:errors.append(type(error).__name__)
def completion():
    try:
        assert held.wait(1)
        if sys.argv[2]=='complete':goals.update(goal.id,state='complete',thread_id='thread')
        else:goals.record_verified_run(record)
    except BaseException as error:errors.append(type(error).__name__)
a=threading.Thread(target=owner,daemon=True);b=threading.Thread(target=completion,name='completion',daemon=True)
a.start();b.start();a.join(1);b.join(1)
print(json.dumps({'blocked':a.is_alive() or b.is_alive(),'errors':errors}),flush=True)
os._exit(0)
'''
        environment={**os.environ,'PYTHONPATH':os.pathsep.join([str(Path(__file__).resolve().parent.parent),
            str(Path(__file__).resolve().parent)])}
        for operation in ('complete','verified_run'):
            with self.subTest(operation=operation),tempfile.TemporaryDirectory() as directory:
                result=subprocess.run([sys.executable,'-c',script,directory,operation],env=environment,
                    capture_output=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr.decode())
                observed=json.loads(result.stdout)
                self.assertEqual(observed,{'blocked':False,'errors':[]})
