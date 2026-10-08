"""Verification owns background work until its file handles have been released."""
import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nailong.core.verification import VerificationService, verify_goal_if_configured


class VerificationWorkerCancellationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        settings = self.root / '.nailong' / 'settings.json'
        settings.parent.mkdir()
        settings.write_text(json.dumps({'verification': {'steps': [
            {'name': 'test', 'kind': 'test', 'command': 'unused'}]}}), encoding='utf-8')
        self.events = []
        self.store = SimpleNamespace(append_event=lambda *args: self.events.append(args))

    async def wait_event(self, event):
        async with asyncio.timeout(3):
            while not event.is_set():
                await asyncio.sleep(.001)

    def blocking_worker(self, failure=None):
        started, release, closed = threading.Event(), threading.Event(), threading.Event()
        held = self.root / 'worker-held.txt'

        def work():
            try:
                with held.open('w', encoding='utf-8'):
                    started.set()
                    if not release.wait(5):
                        raise RuntimeError('Test did not release the verification worker.')
                    if failure is not None:
                        raise failure
                    return 'unchanged'
            finally:
                closed.set()
        return work, started, release, closed, held

    async def cancel_blocked(self, coroutine, started, release, closed):
        task = asyncio.create_task(coroutine)
        caught = None
        try:
            await self.wait_event(started)
            for message in ('first cancellation', 'repeated cancellation'):
                task.cancel(message)
                await asyncio.sleep(.01)
                self.assertFalse(task.done(), 'Cancellation returned while the worker still held a file.')
                self.assertFalse(closed.is_set())
        finally:
            release.set()
            try:
                await task
            except BaseException as error:
                caught = error
            await self.wait_event(closed)
        self.assertIsInstance(caught, asyncio.CancelledError)
        return caught

    async def test_cancellation_joins_validation_and_every_fingerprint_stage(self):
        for stage, blocked_call in (('validation', 0), ('initial', 1), ('approval', 2), ('final', 3)):
            with self.subTest(stage=stage):
                self.events.clear()
                work, started, release, closed, held = self.blocking_worker()
                calls = 0

                def fingerprint(*args):
                    nonlocal calls
                    calls += 1
                    return work() if calls == blocked_call else 'unchanged'

                def validate(*args):
                    if stage == 'validation':
                        work()

                service = VerificationService(self.root, self.store)
                result = {'ok': True, 'started': True, 'exit_code': 0, 'output': '',
                          'timed_out': False, 'observed': None}
                with patch('nailong.core.verification.input_fingerprint', side_effect=fingerprint), \
                        patch('nailong.core.verification._git', return_value=b''), \
                        patch.object(service, '_validate_generated', side_effect=validate), \
                        patch('nailong.core.verification.execute_process', new=AsyncMock(return_value=result)):
                    await self.cancel_blocked(service.run('thread', permission_mode='bypassPermissions'),
                                              started, release, closed)
                held.unlink()
                if stage in {'approval', 'final'}:
                    self.assertEqual(self.events[-1][2]['status'], 'cancelled')
                else:
                    self.assertEqual(self.events, [])

    async def test_goal_fingerprint_cancellation_joins_its_worker(self):
        work, started, release, closed, held = self.blocking_worker()
        goal = SimpleNamespace(id='goal', state='active', verification_tool='verify',
                               verification_succeeded=True, verification_project_root=self.root,
                               verification_generated_paths=[], verification_fingerprint='unchanged')
        factory = SimpleNamespace(settings=SimpleNamespace(project_root=self.root, api_key=''),
                                  goal_store=SimpleNamespace(get=lambda _: goal))
        with patch('nailong.core.verification.input_fingerprint', side_effect=lambda *args: work()):
            await self.cancel_blocked(verify_goal_if_configured(SimpleNamespace(), factory, goal, 'thread'),
                                      started, release, closed)
        held.unlink()

    async def test_worker_failure_is_retained_as_cancellation_cause(self):
        failure = ValueError('Fingerprint failed after cancellation.')
        work, started, release, closed, held = self.blocking_worker(failure)
        service = VerificationService(self.root, self.store)
        with patch('nailong.core.verification.input_fingerprint', side_effect=lambda *args: work()):
            cancelled = await self.cancel_blocked(service.run('thread'), started, release, closed)
        self.assertIs(cancelled.__cause__, failure)
        held.unlink()

    async def test_worker_error_without_cancellation_is_unchanged(self):
        failure = ValueError('Fingerprint failed.')
        service = VerificationService(self.root, self.store)
        with patch('nailong.core.verification.input_fingerprint', side_effect=failure):
            with self.assertRaises(ValueError) as caught:
                await service.run('thread')
        self.assertIs(caught.exception, failure)
