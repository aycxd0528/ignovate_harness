import tempfile
import unittest
from pathlib import Path

from nailong.core.sessions import ProjectSessionStore
from nailong.core.control_ledger import ControlLedger


class ControlLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'project'
        self.project.mkdir()
        self.sessions = ProjectSessionStore(self.project, base_dir=Path(self.temp.name) / 'data')
        self.ledger = ControlLedger(self.sessions)

    def test_stage_retry_is_idempotent_and_survives_restart(self):
        self.ledger.begin('run1', thread_id='thread1', request_id='request1')
        first = self.ledger.record('run1', 'effects_observed', details={'status': 'unknown'})
        self.assertEqual(first, self.ledger.record('run1', 'effects_observed', details={'status': 'unknown'}))
        recovered = ControlLedger(self.sessions).get('run1')
        self.assertEqual([s['stage'] for s in recovered['stages']], ['started', 'effects_observed'])
        self.assertEqual(recovered['recovery'], 'readback_required')

    def test_conflicting_identity_and_stage_are_rejected(self):
        self.ledger.begin('run1', thread_id='thread1', request_id='request1')
        with self.assertRaises(ValueError):
            self.ledger.begin('run1', thread_id='thread2', request_id='request1')
        self.ledger.record('run1', 'cost_settled', details={'usage_ref': 'session:thread1', 'calls': 2})
        with self.assertRaises(ValueError):
            self.ledger.record('run1', 'cost_settled', details={'usage_ref': 'session:thread1', 'calls': 3})

    def test_closeout_does_not_invent_validation_or_cost(self):
        self.ledger.begin('run1', thread_id='thread1', request_id='request1')
        row = self.ledger.record('run1', 'closed', details={'status': 'failed'})
        self.assertEqual(row['recovery'], 'usage_unknown')
        self.assertNotIn('cost_settled', [s['stage'] for s in row['stages']])
        self.assertNotIn('evidence_validated', [s['stage'] for s in row['stages']])

    def test_invalid_identity_and_symlink_fail_closed(self):
        with self.assertRaises(ValueError):
            self.ledger.begin('../escape', thread_id='thread1', request_id='request1')
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        target = self.sessions.root / 'control'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.ledger.begin('run1', thread_id='thread1', request_id='request1')
        self.assertEqual(list(outside.iterdir()), [])
