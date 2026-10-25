import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from nailong.core.goal import GoalStore


class GoalStoreRegressions(unittest.TestCase):
    def test_invalid_goal_ledger_cannot_be_replaced_with_fresh_allowance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'goals.json'
            store = GoalStore(path, api_key='private-key')
            goal = store.create('work', thread_id='owner')
            store.record_round(goal.id, cost_usd=.2, files_changed=True, tool_calls=1,
                               round_id='settled')
            valid = json.loads(path.read_text())[0]
            invalid_records = [
                {}, [valid, {'id': 'broken', 'objective': 'private-key'}],
                [valid, valid], [{**valid, 'state': 'unknown'}],
                [{**valid, 'round': True}], [{**valid, 'spent_usd': -1}],
                [{**valid, 'spent_usd': float('nan')}],
                [{**valid, 'max_cost_usd': float('inf')}],
                [{**valid, 'settled_round_ids': 'settled'}],
            ]
            contents = [b'{"private-key":', b'', b'\xff'] + [
                json.dumps(value).encode() for value in invalid_records]
            for content in contents:
                with self.subTest(content=content):
                    path.write_bytes(content)
                    fresh = GoalStore(path, api_key='private-key')
                    for operation in (fresh.active, lambda: fresh.create('replacement'),
                                      lambda: fresh.record_round(goal.id, cost_usd=.1,
                                          files_changed=True, tool_calls=1)):
                        with self.assertRaises(ValueError) as caught:
                            operation()
                        self.assertNotIn('private-key', str(caught.exception))
                        self.assertEqual(path.read_bytes(), content)

    def test_unreadable_goal_ledger_is_not_treated_as_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'goals.json'
            store = GoalStore(path)
            store.create('work')
            original = path.read_bytes()
            with patch('nailong.core.goal.open_regular_file', side_effect=PermissionError('denied')):
                with self.assertRaises(ValueError):
                    store.create('replacement')
            self.assertEqual(path.read_bytes(), original)

    def test_legacy_goal_defaults_preserve_cost_and_settlement_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'goals.json'
            store = GoalStore(path)
            goal = store.create('work', thread_id='owner')
            store.record_round(goal.id, cost_usd=.2, files_changed=True, tool_calls=1,
                               round_id='settled')
            rows = json.loads(path.read_text())
            for key in ('last_progress', 'recent_progress', 'no_change_rounds'):
                rows[0].pop(key)
            path.write_text(json.dumps(rows))
            fresh = GoalStore(path)
            current = fresh.record_round(goal.id, cost_usd=.2, files_changed=True,
                                         tool_calls=1, round_id='settled')
            self.assertEqual(current.round, 1)
            self.assertAlmostEqual(current.spent_usd, .2)
            self.assertEqual(current.settled_round_ids, ['settled'])

    def test_goal_driver_lease_rejects_second_driver_and_session_rebinding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'goals.json'
            first, second = GoalStore(path), GoalStore(path)
            goal = first.create('work', thread_id='owner')
            with first.driver_lease():
                with self.assertRaisesRegex(ValueError, '运行'):
                    with second.driver_lease(): pass
                with self.assertRaises(ValueError): second.attach_thread(goal.id, 'intruder')
                self.assertEqual(second.get(goal.id).thread_id, 'owner')
            with second.driver_lease():
                self.assertEqual(second.attach_thread(goal.id, 'next').thread_id, 'next')

    def test_intervening_successful_round_breaks_blocker_streak(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory)/'goals.json')
            goal = store.create('work')
            for _ in range(3):
                accepted, _, current = store.update(goal.id, state='blocked', blocker='network down')
                self.assertFalse(accepted)
                self.assertEqual(current.blocked_rounds, 1)
                store.record_round(goal.id, cost_usd=0, files_changed=True, tool_calls=1)
                store.record_round(goal.id, cost_usd=0, files_changed=True, tool_calls=1)

    def test_resume_starts_a_fresh_blocker_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory)/'goals.json')
            goal = store.create('work')
            for _ in range(2):
                store.update(goal.id, state='blocked', blocker='network down')
                store.record_round(goal.id, cost_usd=0, files_changed=False, tool_calls=1)
            store.update(goal.id, state='paused')
            self.assertTrue(store.resume(goal.id)[0])
            accepted, _, current = store.update(goal.id, state='blocked', blocker='network down')
            self.assertFalse(accepted)
            self.assertEqual(current.blocked_rounds, 1)

    def test_separate_stores_do_not_lose_concurrent_cost_settlements(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'goals.json'
            first, second = GoalStore(path), GoalStore(path)
            goal = first.create('work', max_cost_usd=.3)
            start = threading.Barrier(2)
            save = GoalStore._save
            def slow_save(store, goals):
                time.sleep(.03)
                save(store, goals)
            def settle(store, identity):
                start.wait(timeout=3)
                return store.record_round(goal.id, cost_usd=.2, files_changed=True,
                                          tool_calls=1, round_id=identity)
            with patch.object(GoalStore, '_save', slow_save), concurrent.futures.ThreadPoolExecutor(2) as pool:
                calls = [pool.submit(settle, first, 'first'), pool.submit(settle, second, 'second')]
                for call in calls: call.result(timeout=5)
            current = first.get(goal.id)
            self.assertAlmostEqual(current.spent_usd, .4)
            self.assertEqual(current.round, 2)
            self.assertEqual(set(current.settled_round_ids), {'first', 'second'})
            self.assertEqual(current.state, 'paused')
