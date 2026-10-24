"""A cancelled queue controller cannot abandon the work it is closing."""
import asyncio
import contextvars
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
from typing import Any
import unittest

from nailong.core.runner import SessionRunner


class RunnerOwnedStopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runner=SessionRunner('project','thread')
        self.addAsyncCleanup(self.runner.close)

    async def gated_current(self, *, suppress=False):
        started=asyncio.Event();cleaning=asyncio.Event();release=asyncio.Event();closed=asyncio.Event()
        cancellations=[]
        async def execute():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellations.append('requested')
                cleaning.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancellations.append('cleanup interrupted')
                    raise
                finally:
                    closed.set()
                if not suppress:raise
                return 'cleaned result'
        first=self.runner.submit('first',execute)
        self.addCleanup(release.set)
        await asyncio.wait_for(started.wait(),1)
        return first,cleaning,release,closed,cancellations

    def owned(self,coroutine):
        pending=asyncio.create_task(coroutine)
        async def finish():await asyncio.gather(pending,return_exceptions=True)
        self.addAsyncCleanup(finish)
        return pending

    async def test_cancelled_close_waits_for_child_before_external_resources_close(self):
        first,cleaning,release,closed,cancellations=await self.gated_current()
        queued=self.runner.submit('queued',lambda:asyncio.sleep(0))
        external_closed=asyncio.Event()
        async def shutdown():
            try:await self.runner.close()
            finally:external_closed.set()
        closing=self.owned(shutdown())
        try:
            await asyncio.wait_for(cleaning.wait(),1)
            for _ in range(3):
                closing.cancel();await asyncio.sleep(0)
                self.assertFalse(closing.done(),'close returned before current cleanup')
                self.assertFalse(external_closed.is_set())
                self.assertFalse(closed.is_set())
        finally:release.set()
        with self.assertRaises(asyncio.CancelledError):await closing
        self.assertTrue(closed.is_set());self.assertTrue(external_closed.is_set())
        self.assertEqual(cancellations,['requested']);self.assertTrue(first.cancelled())
        self.assertTrue(queued.cancelled());self.assertEqual(self.runner.queue,[])
        self.assertFalse(self.runner.busy)

    async def test_concurrent_stop_and_cancelled_waiter_share_one_child_cancel(self):
        first,cleaning,release,closed,cancellations=await self.gated_current(suppress=True)
        seen=[]
        async def following():seen.append('next');return 'next'
        next_item=self.runner.submit('next',following)
        one=self.owned(self.runner.stop());two=self.owned(self.runner.stop())
        try:
            await asyncio.wait_for(cleaning.wait(),1)
            for _ in range(3):
                one.cancel();await asyncio.sleep(0)
                self.assertFalse(one.done());self.assertFalse(two.done())
            with self.assertRaises(ValueError):self.runner.resume()
            with self.assertRaises(ValueError):self.runner.rebind('other','thread')
        finally:release.set()
        with self.assertRaises(asyncio.CancelledError):await one
        await two
        self.assertTrue(closed.is_set());self.assertEqual(cancellations,['requested'])
        self.assertEqual(await first,'cleaned result');self.assertEqual(seen,[])
        self.assertFalse(next_item.done());self.assertEqual(self.runner.state,'paused')
        self.runner.resume();self.assertEqual(await next_item,'next')

    async def test_resume_during_pump_completion_cannot_start_work_before_stop_returns(self):
        first,cleaning,release,closed,cancellations=await self.gated_current(suppress=True)
        seen=[];attempts=[]
        async def following():seen.append('next');return 'next'
        next_item=self.runner.submit('next',following)
        def attempt_resume(_):
            try:self.runner.resume()
            except ValueError:attempts.append('rejected')
            else:attempts.append('accepted')
        self.runner._pump_task.add_done_callback(attempt_resume)
        stopping=self.owned(self.runner.stop())
        await asyncio.wait_for(cleaning.wait(),1);release.set();await stopping
        self.assertEqual(attempts,['rejected']);self.assertEqual(seen,[])
        self.assertFalse(next_item.done());self.assertEqual(self.runner.state,'paused')
        self.runner.resume();self.assertEqual(await next_item,'next')

    async def test_cancelled_pending_future_never_invokes_callback(self):
        self.runner.state='paused';seen=[]
        async def execute(name):seen.append(name);return name
        withdrawn=self.runner.submit('withdrawn',lambda:execute('withdrawn'))
        kept=self.runner.submit('kept',lambda:execute('kept'))
        withdrawn.cancel();self.runner.resume();await self.runner.wait_idle()
        self.assertTrue(withdrawn.cancelled());self.assertEqual(await kept,'kept')
        self.assertEqual(seen,['kept'])

    async def test_cancelled_pending_items_release_capacity_and_identity_binding(self):
        self.runner.state='paused'
        for index in range(10):
            self.runner.submit(str(index),lambda:asyncio.sleep(0)).cancel()
        # Cancellation callbacks run on the next loop tick. Admission must
        # already exclude these terminal inputs from its ten-item limit.
        kept=self.runner.submit('replacement',lambda:asyncio.sleep(0))
        self.assertEqual(len(self.runner.queue),1)
        kept.cancel();await asyncio.sleep(0)
        self.assertEqual(self.runner.queue,[]);self.assertFalse(self.runner.busy)
        self.runner.rebind('other','thread')
        await self.runner.close()
        with self.assertRaises(ValueError):self.runner.rebind('project','thread')

    async def test_late_running_future_cancel_cannot_race_pump_into_following_work(self):
        for failure in (False,True):
            with self.subTest(failure=failure):
                seen=[]
                async def current():
                    asyncio.get_running_loop().call_soon(first.cancel)
                    if failure:raise ValueError('last operation failed')
                    return 'done'
                async def following():seen.append('next');return 'next'
                first=self.runner.submit('current',current)
                queued=self.runner.submit('next',following)
                if failure:
                    with self.assertRaisesRegex(ValueError,'last operation failed'):await self.runner.wait_idle()
                else:await self.runner.wait_idle()
                self.assertTrue(first.cancelled());self.assertEqual(seen,[])
                self.assertFalse(queued.done());self.assertEqual(self.runner.state,'paused')
                self.runner.resume();self.assertEqual(await queued,'next')

    async def test_current_input_cannot_join_its_own_stop_close_or_idle(self):
        # A self-join cycle can also prevent asyncio.run shutdown. Bound this
        # regression in an actual child, whose explicit exit avoids leaked tasks.
        script='''
import asyncio,json,os,sys
from nailong.core.runner import SessionRunner
async def main():
    runner=SessionRunner('project','thread')
    other=SessionRunner('project','nested')
    async def invoke():
        method=getattr(runner,sys.argv[1])
        if sys.argv[2]=='task':await asyncio.create_task(method())
        elif sys.argv[2]=='group':
            async with asyncio.TaskGroup() as group:group.create_task(method())
        elif sys.argv[2]=='nested':await other.submit('nested control',method)
        else:await method()
    first=runner.submit('self control',invoke)
    done,_=await asyncio.wait({first},timeout=.6)
    if not done:
        print('self-join blocked',flush=True);os._exit(73)
    error=first.exception()
    def errors(error):
        return [type(error).__name__] if not isinstance(error,BaseExceptionGroup) else [name for inner in error.exceptions for name in errors(inner)]
    print(json.dumps({'errors':errors(error),'state':runner.state}),flush=True)
    await runner.close();await other.close()
asyncio.run(main())
'''
        for method in ('stop','close','wait_idle'):
            for mode in ('direct','task','group','nested'):
                with self.subTest(method=method,mode=mode):
                    result=await asyncio.to_thread(subprocess.run,[sys.executable,'-c',script,method,mode],
                        capture_output=True,text=True,timeout=5)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    self.assertIn('RuntimeError',json.loads(result.stdout)['errors'])

    async def test_stale_business_context_can_control_a_later_input(self):
        contexts=[];started=asyncio.Event()
        async def first():contexts.append(contextvars.copy_context())
        async def following():started.set();await asyncio.Event().wait()
        one=self.runner.submit('first',first);two=self.runner.submit('following',following)
        await one;await asyncio.wait_for(started.wait(),1)
        controller=contexts[0].run(asyncio.create_task,self.runner.stop())
        await asyncio.wait_for(controller,1)
        self.assertTrue(two.cancelled());self.assertEqual(self.runner.state,'paused')

    async def test_plain_windows_interrupt_uses_registration_context_to_stop_business_input(self):
        from unittest.mock import patch
        from ui.plain import install_plain_interrupt
        scope=contextvars.ContextVar('test_interrupt_scope',default='control')
        observed=[];stoppers=[];seen=asyncio.Event()
        def interrupt():
            observed.append(scope.get());seen.set()
            stoppers.append(asyncio.create_task(self.runner.stop()))
        # Exercise the Python signal dispatch branch without replacing global
        # os.name or installing a real process signal handler on this platform.
        with patch('ui.plain.os',SimpleNamespace(name='nt')),patch('signal.signal') as register:
            restore=install_plain_interrupt(interrupt)
            handler=register.call_args.args[1]
            async def execute():
                token=scope.set('business')
                try:
                    handler(None,None)
                    await asyncio.Event().wait()
                finally:scope.reset(token)
            future=self.runner.submit('interrupt',execute)
            try:
                await asyncio.wait_for(seen.wait(),1)
                self.assertEqual(observed,['control'])
                await asyncio.gather(*stoppers)
                await self.runner.wait_idle()
                self.assertTrue(future.cancelled());self.assertEqual(self.runner.state,'paused')
            finally:
                restore();await self.runner.stop()
                await asyncio.gather(*stoppers,return_exceptions=True)

    async def test_cancelled_current_cleanup_error_is_reported_by_every_joiner(self):
        runner=SessionRunner('project','errors');started=asyncio.Event()
        async def execute():
            started.set()
            try:await asyncio.Event().wait()
            finally:raise ValueError('cleanup failed after cancellation')
        first=runner.submit('first',execute)
        try:
            await started.wait();first.cancel()
            for join in (runner.wait_idle,runner.stop,runner.close):
                with self.assertRaisesRegex(ValueError,'cleanup failed after cancellation'):await join()
            self.assertTrue(first.cancelled());self.assertFalse(runner.busy)
        finally:
            try:await runner.close()
            except ValueError:pass

    async def test_cancelled_close_retains_late_cleanup_failure_as_its_cause(self):
        runner=SessionRunner('project','errors');started=asyncio.Event();cleaning=asyncio.Event();release=asyncio.Event()
        async def execute():
            started.set()
            try:await asyncio.Event().wait()
            finally:
                cleaning.set();await release.wait()
                raise ValueError('late cleanup failure')
        first=runner.submit('first',execute);closing=None
        try:
            await started.wait();first.cancel();await cleaning.wait()
            closing=asyncio.create_task(runner.close());await asyncio.sleep(0)
            closing.cancel();await asyncio.sleep(0);release.set()
            with self.assertRaises(asyncio.CancelledError) as caught:await closing
            self.assertIsInstance(caught.exception.__cause__,ValueError)
            self.assertEqual(str(caught.exception.__cause__),'late cleanup failure')
        finally:
            release.set()
            if closing is not None:await asyncio.gather(closing,return_exceptions=True)
            try:await runner.close()
            except ValueError:pass

    async def test_running_future_cancel_stops_current_and_requires_resume_for_queue(self):
        first,cleaning,release,closed,cancellations=await self.gated_current()
        seen=[]
        async def following():seen.append('next');return 'next'
        queued=self.runner.submit('next',following)
        first.cancel()
        try:
            await asyncio.wait_for(cleaning.wait(),1)
            waiting=self.owned(self.runner.wait_idle());await asyncio.sleep(0)
            self.assertFalse(waiting.done());self.assertTrue(self.runner.busy)
            self.assertEqual(seen,[])
        finally:release.set()
        await waiting
        self.assertTrue(closed.is_set());self.assertEqual(cancellations,['requested'])
        self.assertTrue(first.cancelled());self.assertEqual(self.runner.state,'paused')
        self.assertFalse(queued.done());self.assertEqual(seen,[])
        self.runner.resume();self.assertEqual(await queued,'next')

    async def test_close_withdraws_pending_futures_while_cleanup_is_still_running(self):
        first,cleaning,release,closed,cancellations=await self.gated_current()
        queued=self.runner.submit('next',lambda:asyncio.sleep(0))
        closing=self.owned(self.runner.close())
        try:
            await asyncio.wait_for(cleaning.wait(),1)
            self.assertTrue(queued.cancelled());self.assertEqual(self.runner.queue,[])
            with self.assertRaises(ValueError):self.runner.submit('late',lambda:asyncio.sleep(0))
            with self.assertRaises(ValueError):self.runner.resume()
            self.assertFalse(closing.done());self.assertFalse(closed.is_set())
        finally:release.set()
        await closing;self.assertFalse(self.runner.busy)

    async def test_immediate_stop_before_pump_starts_preserves_queue_until_explicit_resume(self):
        seen=[]
        async def execute():seen.append('executed');return 'done'
        future=self.runner.submit('queued',execute)
        await self.runner.stop()
        self.assertEqual(seen,[]);self.assertFalse(future.done())
        self.assertEqual(self.runner.state,'paused');self.runner.resume()
        self.assertEqual(await future,'done')
        await self.runner.close();await self.runner.close()
        self.assertFalse(self.runner.busy)

    async def test_failed_current_cleanup_settles_future_and_close_cancels_remaining_queue(self):
        started=asyncio.Event()
        async def execute():
            started.set()
            try:await asyncio.Event().wait()
            finally:raise ValueError('cleanup failed')
        first=self.runner.submit('first',execute);await started.wait()
        queued=self.runner.submit('next',lambda:asyncio.sleep(0))
        await self.runner.close()
        with self.assertRaisesRegex(ValueError,'cleanup failed'):await first
        self.assertTrue(queued.cancelled());self.assertFalse(self.runner.busy)


class NativeRunnerProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_shutdown_keeps_native_factory_and_checkpoint_open_until_graph_cleanup(self):
        from unittest.mock import patch
        from agent import AgentRuntimeFactory
        from agent_service import AgentService
        from config import Settings
        from nailong.core.sessions import ProjectSessionStore
        from test_context_runtime import RecordingModel
        class CleaningModel(RecordingModel):
            started:Any=None
            cleaning:Any=None
            release:Any=None
            async def _agenerate(self,*args,**kwargs):
                self.started.set()
                try:await asyncio.Event().wait()
                finally:
                    self.cleaning.set();await self.release.wait()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()/'project';root.mkdir()
            store=ProjectSessionStore(root,base_dir=root.parent/'private')
            model=CleaningModel(responses=[],started=asyncio.Event(),cleaning=asyncio.Event(),release=asyncio.Event())
            with patch('agent.ChatDeepSeek',return_value=model):
                factory=AgentRuntimeFactory(Settings('key','https://example.invalid','private',root),session_store=store)
            runner=SessionRunner(str(root),'thread');shutdown=None;factory_closed=asyncio.Event()
            try:
                runtime=await factory.async_runtime(thread_id='thread',allowed_tools=set())
                service=AgentService(factory);config={'configurable':{'thread_id':'thread'}}
                runner.submit('native graph',lambda:service.run_turn('你好',config,allowed_tools=set()))
                queued=runner.submit('pending',lambda:asyncio.sleep(0))
                await asyncio.wait_for(model.started.wait(),3)
                async def close_in_native_order():
                    try:await runner.close()
                    finally:
                        await factory.aclose();factory.close();factory_closed.set()
                shutdown=asyncio.create_task(close_in_native_order())
                await asyncio.wait_for(model.cleaning.wait(),3)
                for _ in range(3):
                    shutdown.cancel();await asyncio.sleep(0)
                    self.assertFalse(shutdown.done());self.assertFalse(factory_closed.is_set())
                    self.assertIsNotNone(factory.async_checkpointer)
                # Read the real SQLite checkpoint during model cleanup; a
                # prematurely closed factory would fail this operation.
                state=await runtime.aget_state(config)
                self.assertTrue(state.values.get('messages'))
                model.release.set()
                with self.assertRaises(asyncio.CancelledError):await shutdown
                self.assertTrue(factory_closed.is_set());self.assertTrue(queued.cancelled())
                self.assertFalse(runner.busy)
            finally:
                model.release.set()
                if shutdown is not None:await asyncio.gather(shutdown,return_exceptions=True)
                await runner.close();await factory.aclose();factory.close()

    async def test_future_cancellation_joins_native_tree_and_keeps_following_input_paused(self):
        from nailong.core.processes import execute_process
        from test_native_processes import shell_python
        from platform_fixtures import process_exists
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);started=root/'started.json';late=root/'late';following=root/'following'
            child=f"import time;from pathlib import Path;time.sleep(.8);Path({str(late)!r}).write_text('leaked')"
            code=("import json,os,subprocess,sys,time;from pathlib import Path;"
                  f"child=subprocess.Popen([sys.executable,'-c',{child!r}]);"
                  f"info=Path({str(started)!r});info.with_suffix('.tmp').write_text(json.dumps([os.getpid(),child.pid]));"
                  "info.with_suffix('.tmp').replace(info);time.sleep(30)")
            runner=SessionRunner(str(root),'thread')
            try:
                current=runner.submit('process',lambda:execute_process(shell_python(code),root))
                async def next_item():following.write_text('ran')
                queued=runner.submit('next',next_item)
                async with asyncio.timeout(5):
                    while not started.exists():await asyncio.sleep(.01)
                pids=json.loads(started.read_text());current.cancel()
                await asyncio.wait_for(runner.wait_idle(),3)
                self.assertTrue(current.cancelled());self.assertEqual(runner.state,'paused')
                self.assertFalse(queued.done());self.assertFalse(following.exists())
                self.assertTrue(all(not process_exists(pid) for pid in pids))
                await asyncio.sleep(1);self.assertFalse(late.exists())
                runner.resume();await queued;self.assertTrue(following.exists())
            finally:await runner.close()


if __name__=='__main__':unittest.main()
