"""Session recap and export keep their filesystem observation off-loop."""
import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from nailong.core.session_actions import SessionActions
from nailong.core.sessions import ProjectSessionStore
from nailong.core.verification import input_fingerprint


class SessionActionScanTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'project'
        self.root.mkdir()
        (self.root/'source.py').write_text('source')
        self.store = ProjectSessionStore(self.root, base_dir=Path(self.temp.name)/'data')
        self.store.append_event('one', 'final', {'text': 'saved'})
        self.verification = {'status': 'passed', 'input_after': input_fingerprint(self.root),
                             'generated_paths': [], 'steps': []}
        self.store.append_event('one', 'verification', self.verification)
        self.actions = SessionActions(self.root, self.store)

    async def test_export_observation_leaves_event_loop_responsive(self):
        loop = asyncio.get_running_loop()
        responsive = []
        def scan(root, generated=()):
            tick = threading.Event()
            loop.call_soon_threadsafe(tick.set)
            responsive.append(tick.wait(.5))
            return input_fingerprint(root, generated)
        with patch('nailong.core.verification.input_fingerprint', side_effect=scan):
            exported = await self.actions.export('one')
        self.assertTrue(exported['written'])
        self.assertEqual(responsive, [True], 'export blocked its owning event loop')
        self.assertIn('已验证：', Path(exported['path']).read_text())

    async def test_recap_never_applies_an_observation_to_a_new_verification_record(self):
        loop = asyncio.get_running_loop()
        changed = threading.Event()
        def replace():
            self.store.append_event('one', 'verification', {
                **self.verification, 'run_id': 'new-record-during-observation'})
            changed.set()
        def scan(root, generated=()):
            value = input_fingerprint(root, generated)
            loop.call_soon_threadsafe(replace)
            if not changed.wait(2):
                raise AssertionError('verification replacement blocked by observation')
            return value
        with patch('nailong.core.verification.input_fingerprint', side_effect=scan):
            recap = await self.actions.recap_async('one')
        self.assertIn('未验证：', recap)
        self.assertNotIn('已验证：', recap)
