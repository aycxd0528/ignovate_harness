"""Stop receipts through the actual plain UI, graph, checkpoint and runner."""
import asyncio
import gc
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from typing import Any
from unittest.mock import patch

from rich.console import Console as RichConsole

from agent import AgentRuntimeFactory
from config import Settings
from nailong.core.runner import SessionRunner
from test_context_runtime import RecordingModel
from ui.app import run_inline
from ui.console import Console
from ui.plain import PlainConsole
from ui.plain import install_plain_interrupt as native_install_interrupt
from ui.theme import Theme


class GatedModel(RecordingModel):
    started: Any = None
    cleaning: Any = None
    release: Any = None
    cleaned: Any = None

    async def _agenerate(self, *args, **kwargs):
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cleaning.set()
            await self.release.wait()
            self.cleaned.set()


class Prompt:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.buffer = b'draft'

    async def prompt_async(self, *_args, **_kwargs):
        answer = await self.messages.get()
        if isinstance(answer, BaseException):
            raise answer
        return answer


class CapturedConsole(PlainConsole):
    def __init__(self):
        self.output = io.StringIO()
        self.receipts = []
        Console.__init__(self, theme=Theme(no_color=True),
                         console=RichConsole(file=self.output, no_color=True, width=160))

    def print(self, renderable, **kwargs):
        self.receipts.append(str(renderable))
        super().print(renderable, **kwargs)


class TerminalStopReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve() / 'project'
        self.root.mkdir()
        self.key = 'terminal-stop-secret'
        self.model = GatedModel(responses=[], started=asyncio.Event(), cleaning=asyncio.Event(),
                                release=asyncio.Event(), cleaned=asyncio.Event())
        self.prompt = Prompt()
        self.console = CapturedConsole()
        self.factories, self.runners, self.interrupts = [], [], []
        self.signal_restored = asyncio.Event()
        self.loop_errors = []
        self.loop = asyncio.get_running_loop()
        old_handler = self.loop.get_exception_handler()
        self.loop.set_exception_handler(lambda _loop, context: self.loop_errors.append(context))
        self.addCleanup(self.loop.set_exception_handler, old_handler)

        def factory(settings):
            result = AgentRuntimeFactory(settings)
            self.factories.append(result)
            return result

        def runner(*args):
            result = SessionRunner(*args)
            self.runners.append(result)
            return result

        def install(callback):
            self.interrupts.append(callback)
            if os.environ.get('IGNOVATE_TEST_NATIVE_STOP_SIGNAL') == '1':
                restore = native_install_interrupt(callback)
                def restored():
                    restore()
                    self.signal_restored.set()
                return restored
            return self.signal_restored.set

        for replacement in (
            patch.dict(os.environ, {'NAILONG_DATA_DIR': str(self.root.parent / 'private')}),
            patch('agent.ChatDeepSeek', return_value=self.model),
            patch('ui.app.AgentRuntimeFactory', factory),
            patch('ui.app.SessionRunner', runner),
            patch('ui.plain.install_plain_interrupt', install),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.prompt.messages.put_nowait('开始需要停止的长任务')
        self.ui = asyncio.create_task(run_inline(
            Settings(self.key, 'https://api.invalid', 'deepseek-flash', self.root),
            plain=True, prompt_session=self.prompt, console=self.console))
        self.addAsyncCleanup(self.finish_ui)
        await asyncio.wait_for(self.model.started.wait(), 5)
        self.runner = self.runners[0]
        self.factory = self.factories[0]

    async def finish_ui(self):
        self.model.release.set()
        self.prompt.messages.put_nowait('/exit')
        try:
            await asyncio.wait_for(asyncio.shield(self.ui), 5)
        except (Exception, asyncio.CancelledError):
            if not self.ui.done():
                self.ui.cancel()
            await asyncio.gather(self.ui, return_exceptions=True)

    async def settle(self):
        for _ in range(20):
            await asyncio.sleep(0)

    async def test_plain_receipt_waits_native_cleanup_and_repeated_signals_share_stop(self):
        # A printed completion before this real provider finally gate is false.
        goal = self.factory.goal_store.create('完成复杂任务', thread_id=self.runner.thread_id)
        self.interrupts[0]()
        with self.assertRaises(ValueError):
            self.runner.resume()
        await asyncio.wait_for(self.model.cleaning.wait(), 3)
        self.assertFalse(self.model.cleaned.is_set())
        self.assertFalse(any('当前任务已停止' in row for row in self.console.receipts))
        self.assertEqual(self.factory.goal_store.get(goal.id).state, 'paused')
        runtime = await self.factory.async_runtime(thread_id=self.runner.thread_id, allowed_tools=set())
        state = await runtime.aget_state({'configurable': {'thread_id': self.runner.thread_id}})
        self.assertTrue(state.values.get('messages'))
        for _ in range(3):
            self.interrupts[0]()
        self.model.release.set()
        await self.runner.wait_idle()
        await self.settle()
        self.assertEqual(sum('当前任务已停止' in row for row in self.console.receipts), 1)
        self.assertEqual(self.loop_errors, [])

    async def test_pause_storage_error_is_reported_without_escaping_signal_callback(self):
        # Persistence failure must not turn into an unredacted loop callback error.
        with patch('ui.app.pause_session_goal', side_effect=ValueError('pause failed ' + self.key)):
            self.loop.call_soon(self.interrupts[0])
            await asyncio.wait_for(self.model.cleaning.wait(), 3)
            await self.settle()
            self.assertEqual(self.loop_errors, [])
            self.assertIn('pause failed', self.console.output.getvalue())
            self.assertNotIn(self.key, self.console.output.getvalue())
        self.model.release.set()
        await self.runner.wait_idle()

    async def test_stop_error_is_consumed_redacted_and_never_claims_completion(self):
        # Inject a control-boundary failure after the actual graph/runner join;
        # the UI still owns and reports the controller instead of leaking it.
        real_finish = self.runner._finish_stop

        async def failed_stop(pump):
            await real_finish(pump)
            raise RuntimeError('stop failed ' + self.key)

        with patch.object(self.runner, '_finish_stop', failed_stop):
            self.interrupts[0]()
            await asyncio.wait_for(self.model.cleaning.wait(), 3)
            self.model.release.set()
            with self.assertRaisesRegex(RuntimeError, 'stop failed'):
                await self.runner.wait_idle()
            await self.settle()
            self.assertEqual(self.loop_errors, [])
            self.assertIn('stop failed', self.console.output.getvalue())
            self.assertNotIn(self.key, self.console.output.getvalue())
            self.assertFalse(any('当前任务已停止' in row for row in self.console.receipts))

    async def test_native_ui_shutdown_joins_pending_stop_before_closing_checkpoint(self):
        self.interrupts[0]()
        await asyncio.wait_for(self.model.cleaning.wait(), 3)
        for _ in range(3):
            self.ui.cancel()
            await asyncio.sleep(0)
            self.assertFalse(self.ui.done())
            self.assertIsNotNone(self.factory.async_checkpointer)
        self.model.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await self.ui
        self.assertTrue(self.signal_restored.is_set())
        self.assertIsNone(self.factory.async_checkpointer)
        self.assertFalse(any(task.get_name() == 'terminal:stop' for task in asyncio.all_tasks()))
        self.assertEqual(self.loop_errors, [])

    async def test_resumed_generation_does_not_receive_stale_stop_receipt(self):
        self.interrupts[0]()
        await asyncio.wait_for(self.model.cleaning.wait(), 3)
        resumed = asyncio.Event()

        def resume(_task):
            self.runner.resume()
            resumed.set()

        self.runner._stop_task.add_done_callback(resume)
        self.model.release.set()
        await asyncio.wait_for(resumed.wait(), 3)
        await self.settle()
        self.assertFalse(any('当前任务已停止' in row for row in self.console.receipts))
        self.assertEqual(self.loop_errors, [])

    async def test_cancel_controller_before_first_step_does_not_cancel_normal_exit(self):
        # A never-started controller must release its captured coroutine; its
        # cancellation is not evidence that the UI caller cancelled /exit.
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter('always', RuntimeWarning)
            self.interrupts[0]()
            controller = next(task for task in asyncio.all_tasks() if task.get_name() == 'terminal:stop')
            controller.cancel()
            await asyncio.wait_for(self.model.cleaning.wait(), 3)
            self.model.release.set()
            self.prompt.messages.put_nowait('/exit')
            outcome = (await asyncio.gather(self.ui, return_exceptions=True))[0]
            await self.settle()
            gc.collect()
        self.assertIsNone(outcome)
        self.assertEqual([str(item.message) for item in observed], [])
        self.assertIsNone(self.factory.async_checkpointer)
        self.assertTrue(self.signal_restored.is_set())
        self.assertEqual(self.loop_errors, [])

    @unittest.skipUnless(os.name == 'posix', 'POSIX native SIGINT receipt integration')
    async def test_real_sigint_runs_native_plain_ui_stop_to_completion(self):
        # Keep the actual OS signal and loop handler confined to a subprocess.
        if os.environ.get('IGNOVATE_TEST_NATIVE_STOP_SIGNAL') != '1':
            result = await asyncio.to_thread(subprocess.run,
                [sys.executable, '-m', 'unittest', self.id(), '-q'],
                env={**os.environ, 'IGNOVATE_TEST_NATIVE_STOP_SIGNAL': '1',
                     'PYTHONPATH': os.pathsep.join(filter(None, (
                         os.environ.get('PYTHONPATH', ''), str(Path(__file__).resolve().parent))))},
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            return
        signal.raise_signal(signal.SIGINT)
        await asyncio.wait_for(self.model.cleaning.wait(), 3)
        self.assertFalse(any('当前任务已停止' in row for row in self.console.receipts))
        signal.raise_signal(signal.SIGINT)
        self.model.release.set()
        await self.runner.wait_idle()
        await self.settle()
        self.assertEqual(sum('当前任务已停止' in row for row in self.console.receipts), 1)
        self.assertEqual(self.loop_errors, [])


class BeginStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_begin_stop_sets_barrier_synchronously_before_waiter_is_scheduled(self):
        # Merely scheduling async stop leaves an admission window in a signal callback.
        runner = SessionRunner('project', 'thread')
        self.addAsyncCleanup(runner.close)
        started, release = asyncio.Event(), asyncio.Event()
        self.addCleanup(release.set)

        async def work():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await release.wait()

        runner.submit('work', work)
        await started.wait()
        self.assertTrue(callable(getattr(runner, 'begin_stop', None)))
        waiting = runner.begin_stop()
        try:
            with self.assertRaises(ValueError):
                runner.resume()
        finally:
            release.set()
        self.assertIs(await waiting, True)

    async def test_old_stop_waiter_does_not_stop_or_describe_resumed_generation(self):
        runner = SessionRunner('project', 'thread')
        self.addAsyncCleanup(runner.close)
        runner.state = 'paused'
        self.assertTrue(callable(getattr(runner, 'begin_stop', None)))
        waiting = runner.begin_stop()
        await runner.wait_idle()
        runner.resume()
        completed = runner.submit('new', lambda: asyncio.sleep(0, result='new result'))
        self.assertIs(await waiting, False)
        self.assertEqual(await completed, 'new result')

    async def test_waiter_cannot_be_delegated_to_its_own_business_cleanup(self):
        # Transferring a coroutine created by an external controller must not
        # bypass the self-join guard. A finite subprocess contains a regression.
        source = '''import asyncio,json,os
from nailong.core.runner import SessionRunner
async def main():
    runner=SessionRunner('project','thread')
    started,gate,rejected=asyncio.Event(),asyncio.Event(),asyncio.Event()
    slot={}
    async def work():
        started.set()
        try: await asyncio.Event().wait()
        finally:
            await gate.wait()
            try: await slot['waiter']
            except RuntimeError: rejected.set()
    runner.submit('work',work)
    await started.wait()
    slot['waiter']=runner.begin_stop()
    gate.set()
    join=asyncio.create_task(runner.wait_idle())
    done,_=await asyncio.wait({join},timeout=.6)
    if not done: os._exit(73)
    await join
    await runner.close()
    print(json.dumps({'rejected':rejected.is_set()}))
asyncio.run(main())
'''
        result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', source],
                                        capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'rejected': True})
