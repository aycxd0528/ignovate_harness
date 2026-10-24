"""Actual SQLite lifecycle, including asyncio.run's direct all-Task cancellation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from nailong.core import runtime_resources

PROBE=Path(__file__).resolve().parent/'fixtures/sqlite_shutdown_probe.py'


class SQLiteGlobalShutdownTests(unittest.TestCase):
    def probe(self,mode):
        with tempfile.TemporaryDirectory(prefix='SQLite shutdown 原生 ') as temporary:
            environment={**os.environ,'PYTHONPATH':str(Path(runtime_resources.__file__).resolve().parents[2])}
            result=subprocess.run([sys.executable,'-B',str(PROBE),temporary,mode],
                capture_output=True,text=True,env=environment,timeout=12)
            self.assertEqual(result.returncode,0,result.stderr)
            facts=json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(result.stderr,'')
            return facts

    def assert_settled(self,facts,*,cancelled=True,connections=1,native_failure=None):
        self.assertTrue(facts['operation_complete_at_loop_exit'],facts)
        self.assertEqual(facts['connections'],connections,facts)
        self.assertEqual(facts['native_closed'],[True]*connections,facts)
        self.assertEqual(facts['unpublished'],0,facts)
        self.assertFalse(facts['published'],facts)
        self.assertEqual(facts['workers_alive'],0,facts)
        self.assertEqual(facts['worker_count'],1,facts)
        self.assertTrue(facts['workers_stopped_at_loop_exit'],facts)
        self.assertTrue(facts['stop_delay_finished_at_loop_exit'],facts)
        self.assertEqual(facts['thread_errors'],[],facts)
        if native_failure is None:self.assertEqual(facts['loop_errors'],[],facts)
        else:
            self.assertTrue(all(error==native_failure for error in facts['loop_errors']),facts)
            self.assertIn(native_failure,json.dumps(facts['operation_errors']))
        self.assertEqual(facts['caller_cancelled'],cancelled,facts)

    def test_global_shutdown_waits_for_native_open_and_closes_retained_handle(self):
        self.assert_settled(self.probe('global-open'))

    def test_public_factory_open_cannot_lose_native_handle_on_global_shutdown(self):
        self.assert_settled(self.probe('factory-open'))

    def test_single_caller_cancellation_still_waits_and_propagates(self):
        self.assert_settled(self.probe('caller-cancel'))

    def test_global_shutdown_during_cancelled_open_discard_finishes_native_close(self):
        self.assert_settled(self.probe('global-discard'))

    def test_global_shutdown_finishes_published_saver_close(self):
        facts=self.probe('global-close')
        self.assert_settled(facts)
        self.assertEqual(set(facts['native_close_tasks']),{'runtime:sqlite-close'},facts)

    def test_global_shutdown_waits_for_cancelled_checkpoint_writer_lock(self):
        self.assert_settled(self.probe('locked-close'))

    def test_repeated_caller_cancel_still_waits_for_native_cleanup(self):
        self.assert_settled(self.probe('repeated-cancel'))

    def test_caller_timeout_is_not_disabled_by_sqlite_owner(self):
        facts=self.probe('caller-timeout')
        self.assert_settled(facts,cancelled=False)
        self.assertTrue(facts['caller_timeout'],facts)

    def test_native_open_failure_during_global_shutdown_keeps_original_cause(self):
        self.assert_settled(self.probe('open-failure'),connections=0,native_failure='native-open-failure')

    def test_setup_failure_during_global_shutdown_closes_real_connection_and_keeps_cause(self):
        self.assert_settled(self.probe('setup-failure'),native_failure='native-setup-failure')

    def test_failed_native_close_retains_handle_until_explicit_retry(self):
        facts=self.probe('close-failure-retry')
        self.assert_settled(facts,cancelled=False)
        self.assertTrue(facts['retry']['published_retained'],facts)
        self.assertTrue(facts['retry']['native_open'],facts)
        self.assertTrue(facts['retry']['proxy_kept_native'],facts)
        self.assertGreaterEqual(facts['retry']['attempts'],1,facts)

    def test_real_open_failure_global_shutdown_joins_native_stop_before_loop_returns(self):
        self.assert_settled(self.probe('open-stop-global'),connections=0,native_failure='unable to open database file')

    def test_real_open_failure_normal_path_joins_native_stop_before_loop_returns(self):
        self.assert_settled(self.probe('open-stop-normal'),connections=0,cancelled=False,native_failure='unable to open database file')

    def test_native_worker_close_failure_keeps_original_thread_and_handle_retryable(self):
        facts=self.probe('worker-close-failure-retry')
        self.assert_settled(facts,cancelled=False)
        self.assertTrue(facts['retry']['published_retained'],facts)
        self.assertTrue(facts['retry']['native_open'],facts)
        self.assertTrue(facts['retry']['proxy_kept_native'],facts)
        self.assertTrue(facts['retry']['worker_retained'],facts)
        self.assertGreaterEqual(facts['retry']['attempts'],3,facts)


class SQLiteLifecycleCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_setup_keeps_context_name_and_original_loop_task_factory(self):
        import asyncio
        import contextvars
        from unittest.mock import patch
        from agent import AsyncSqliteSaver
        value=contextvars.ContextVar('sqlite-owner-fixture',default='missing')
        token=value.set('caller-context');observations=[];created=[]
        owner=runtime_resources.RuntimeResourceOwner();owner._init_resource_owner()
        loop=asyncio.get_running_loop();previous=loop.get_task_factory()
        native=AsyncSqliteSaver.setup
        def factory(current_loop,coroutine,context=None):
            if previous is not None:
                task=previous(current_loop,coroutine,context=context) if context is not None else previous(current_loop,coroutine)
            else:task=asyncio.Task(coroutine,loop=current_loop,context=context)
            created.append(task);return task
        async def setup(saver):
            observations.append((value.get(),asyncio.current_task().get_name()))
            value.set('private-owner-context')
            await native(saver)
        with tempfile.TemporaryDirectory() as temporary:
            loop.set_task_factory(factory)
            try:
                with patch.object(AsyncSqliteSaver,'setup',new=setup):
                    await owner._get_async_saver(lambda:AsyncSqliteSaver.from_conn_string(str(Path(temporary)/'db.sqlite')))
                ordinary=asyncio.create_task(asyncio.sleep(0),name='ordinary-fixture-task');await ordinary
                await owner.aclose()
                self.assertEqual(observations,[('caller-context','runtime:sqlite-open')])
                self.assertEqual(value.get(),'caller-context')
                self.assertIs(loop.get_task_factory(),factory)
                self.assertIn(ordinary,created)
            finally:
                await owner.aclose();loop.set_task_factory(previous);value.reset(token)

    async def test_cancellation_before_request_admission_never_opens_sqlite(self):
        import asyncio
        from agent import AsyncSqliteSaver
        owner=runtime_resources.RuntimeResourceOwner();owner._init_resource_owner()
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'must-not-open.sqlite'
            operation=asyncio.create_task(owner._get_async_saver(lambda:AsyncSqliteSaver.from_conn_string(str(path))))
            operation.cancel();await asyncio.gather(operation,return_exceptions=True)
            self.assertFalse(path.exists());self.assertEqual(owner._unpublished_savers,[])
            self.assertIsNone(owner.async_checkpointer)
            await owner.aclose()

    async def test_repeated_native_open_close_releases_threads_and_descriptors(self):
        import threading
        from agent import AsyncSqliteSaver
        before={thread.ident for thread in threading.enumerate()}
        descriptors=len(os.listdir('/dev/fd')) if Path('/dev/fd').exists() else None
        with tempfile.TemporaryDirectory() as temporary:
            for iteration in range(6):
                owner=runtime_resources.RuntimeResourceOwner();owner._init_resource_owner()
                saver=await owner._get_async_saver(lambda:AsyncSqliteSaver.from_conn_string(str(Path(temporary)/f'{iteration}.sqlite')))
                worker=getattr(saver.conn,'_thread',saver.conn)
                await owner.aclose();worker.join(timeout=1)
                self.assertFalse(worker.is_alive())
                self.assertIsNone(owner.async_checkpointer)
        self.assertTrue({thread.ident for thread in threading.enumerate()}<=before)
        if descriptors is not None:self.assertEqual(len(os.listdir('/dev/fd')),descriptors)
