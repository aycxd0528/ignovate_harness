import concurrent.futures
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from nailong.core.goal import GoalStore


class GoalStoreRegressions(unittest.TestCase):
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
