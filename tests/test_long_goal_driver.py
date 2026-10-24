"""Native autonomous long-task continuation across a runtime restart."""
from pathlib import Path
import tempfile
import unittest
from long_goal_driver_scenario import run_goal_driver_scenario


class LongGoalDriverTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_autonomous_driver_restarts_repairs_verifies_and_delivers(self):
        with tempfile.TemporaryDirectory() as temporary:
            result=await run_goal_driver_scenario(Path(temporary).resolve())
        self.assertEqual(result['rounds'],9)
        self.assertEqual(result['requests'],19)
        self.assertEqual(result['spent_tokens'],2090)
        self.assertTrue(result['compacted'])
        self.assertEqual(result['delivery'],'verified')
        self.assertEqual(result['after_external_change'],'unverified')
