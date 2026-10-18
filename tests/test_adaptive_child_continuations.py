import asyncio,tempfile,unittest
from pathlib import Path
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
from nailong.tools.agents import ReadOnlyTaskRunner,ChildTaskResult

class ChildContinuationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'project';self.root.mkdir();(self.root/'a.txt').write_text('first')
        self.sessions=ProjectSessionStore(self.root,base_dir=Path(self.tmp.name)/'data')
        self.tasks=TaskStore(self.sessions);self.tasks.begin('parent','研究 a.txt')

    async def test_budget_stop_can_resume_later_with_identity_and_evidence(self):
        from nailong.core.child_continuations import ChildContinuations
        calls=[]
        async def worker(prompt):
            calls.append(prompt)
            return ChildTaskResult('待补读' if len(calls)==1 else '完成',{'input_tokens':1,'output_tokens':1},
                budget_failure={'reason':'input_does_not_fit'} if len(calls)==1 else None)
        runner=ReadOnlyTaskRunner(worker,token_budget=30000)
        store=ChildContinuations(self.sessions,self.tasks)
        first=await store.run(runner,'parent','研究 a.txt')
        self.assertEqual(first['status'],'budget_exhausted');self.assertTrue(first['resume_reference'])
        runner.begin_turn('parent')
        final=await store.run(runner,'parent','补全结论',first['resume_reference'])
        self.assertEqual(final['status'],'unverified','legacy/custom result has no native evidence attestation')
        self.assertIn('待补读',calls[-1]);self.assertEqual(final['evidence'],[])
        self.assertFalse((await store.run(runner,'parent','再次',first['resume_reference']))['ok'])

    async def test_resume_rejects_other_thread_changed_files_and_changed_parent_revision(self):
        from nailong.core.child_continuations import ChildContinuations
        async def worker(prompt):return ChildTaskResult('部分',{'input_tokens':1,'output_tokens':1},budget_failure={'reason':'stage'})
        runner=ReadOnlyTaskRunner(worker);store=ChildContinuations(self.sessions,self.tasks)
        result=await store.run(runner,'parent','研究')
        self.assertFalse((await store.run(runner,'other','继续',result['resume_reference']))['ok'])
        (self.root/'a.txt').write_text('changed')
        self.assertFalse((await store.run(runner,'parent','继续',result['resume_reference']))['ok'])

    def test_runtime_options_strict_validation_and_output_caps(self):
        from nailong.core.runtime_options import child_options
        options=child_options(self.root,model_output=2048,context_window=8192)
        self.assertEqual(options['output_tokens'],2048);self.assertEqual(options['final_output_tokens'],1024)
        (self.root/'.nailong').mkdir();(self.root/'.nailong/settings.json').write_text('{"subagents":{"max_concurrency":true}}')
        with self.assertRaises(ValueError):child_options(self.root,model_output=2048,context_window=8192)

    async def test_cancelled_worker_settles_once_and_persists_interrupted_stage(self):
        from nailong.core.child_continuations import ChildContinuations
        started=asyncio.Event()
        async def worker(prompt):
            started.set();await asyncio.Event().wait()
        runner=ReadOnlyTaskRunner(worker);store=ChildContinuations(self.sessions,self.tasks)
        task=asyncio.create_task(store.run(runner,'parent','研究'))
        await started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        rows=list(store._read()['runs'].values())
        self.assertEqual(rows[0]['status'],'interrupted')
        self.assertEqual(runner._budget('parent').reserved,0)

    async def test_failed_initial_persistence_never_starts_worker(self):
        from nailong.core.child_continuations import ChildContinuations
        from unittest.mock import patch
        calls=[]
        async def worker(prompt):calls.append(prompt);return 'text'
        store=ChildContinuations(self.sessions,self.tasks)
        with patch.object(store,'_write',side_effect=OSError('disk full')):
            result=await store.run(ReadOnlyTaskRunner(worker),'parent','研究')
        self.assertFalse(result['ok']);self.assertEqual(calls,[])

    async def test_unknown_usage_cannot_be_reported_as_completed(self):
        from nailong.core.child_continuations import ChildContinuations
        async def worker(prompt):return 'unattested legacy text'
        store=ChildContinuations(self.sessions,self.tasks)
        result=await store.run(ReadOnlyTaskRunner(worker),'parent','研究')
        self.assertEqual(result['status'],'usage_unknown');self.assertEqual(result['evidence'],[])
