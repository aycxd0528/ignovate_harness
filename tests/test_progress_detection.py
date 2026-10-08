"""Conservative progress signals from stable, successful read observations."""

import unittest
import json
import tempfile
from pathlib import Path

from nailong.core.progress import assess_progress


def read(arguments='A', *, result='same', version='v1', tool='read_file', **fields):
    return {'tool': tool, 'arguments_digest': arguments, 'result_digest': result,
            'input_version': version, 'ok': True, 'changed': False, **fields}


def consume(observations, phase='investigate'):
    rows = []
    decisions = []
    for observation in observations:
        decision = assess_progress(rows, observation, phase=phase)
        decisions.append(decision)
        rows = decision['observations']
    return decisions


class ProgressDetectionTests(unittest.TestCase):
    def test_two_read_cycle_hints_after_two_full_cycles_and_pauses_after_four(self):
        decisions = consume([read('A'), read('B')] * 4)
        self.assertEqual([item['action'] for item in decisions],
                         ['none', 'none', 'none', 'hint', 'none', 'none', 'none', 'pause'])
        self.assertEqual(decisions[-1]['repeats'], 8)
        self.assertIn('2', decisions[-1]['reason'])

    def test_three_read_cycle_pauses_after_four_complete_investigation_cycles(self):
        decisions = consume([read('A'), read('B'), read('C')] * 4)
        self.assertEqual([item['action'] for item in decisions],
                         ['none'] * 5 + ['hint'] + ['none'] * 5 + ['pause'])
        self.assertEqual(decisions[-1]['repeats'], 12)

    def test_other_phases_pause_after_three_complete_cycles(self):
        for period in (['A', 'B'], ['A', 'B', 'C']):
            with self.subTest(period=period):
                decisions = consume([read(item) for item in period] * 3, phase='verify')
                self.assertEqual(decisions[len(period) * 2 - 1]['action'], 'hint')
                self.assertEqual(decisions[-1]['action'], 'pause')
                self.assertNotIn('pause', [item['action'] for item in decisions[:-1]])

    def test_consecutive_read_thresholds_remain_unchanged(self):
        self.assertEqual([item['action'] for item in consume([read()] * 6)],
                         ['none', 'none', 'hint', 'none', 'none', 'pause'])
        self.assertEqual([item['action'] for item in consume([read()] * 5, phase='verify')],
                         ['none', 'none', 'hint', 'none', 'pause'])

    def test_cycle_can_follow_an_unrelated_read_without_counting_it(self):
        decisions = consume([read('unrelated')] + [read('A'), read('B')] * 4)
        self.assertEqual(decisions[-1]['action'], 'pause')
        self.assertEqual(decisions[-1]['repeats'], 8)

    def test_mixed_successful_read_tools_can_form_a_stable_cycle(self):
        decisions = consume([read('A', tool='read_file'), read('B', tool='grep')] * 4)
        self.assertEqual(decisions[-1]['action'], 'pause')

    def test_paging_and_distinct_parameters_do_not_trigger_cycles(self):
        observations = [read(f'page-{index}') for index in range(40)]
        self.assertTrue(all(item['action'] == 'none' for item in consume(observations)))

    def test_new_result_or_version_for_each_action_prevents_cycle_pause(self):
        for field in ('result_digest', 'input_version'):
            with self.subTest(field=field):
                observations = []
                for index in range(10):
                    observations.extend([read('A', **{field: f'new-{index}'}), read('B')])
                self.assertTrue(all(item['action'] == 'none' for item in consume(observations)))

    def test_same_request_with_alternating_results_or_versions_is_not_a_cycle(self):
        for field in ('result_digest', 'input_version'):
            with self.subTest(field=field):
                observations = [read('A', **{field: 'first'}), read('A', **{field: 'second'})] * 10
                self.assertTrue(all(item['action'] == 'none' for item in consume(observations)))

    def test_success_status_transition_does_not_count_as_stable_read_cycle(self):
        observations = [read('A', status='success'), read('B', status='success'),
                        read('A', status='passed'), read('B', status='passed')] * 4
        self.assertTrue(all(item['action'] == 'none' for item in consume(observations)))

    def test_failure_unknown_changed_or_command_breaks_cycle_history(self):
        barriers = [read('gap', ok=False), read('gap', ok=None), read('gap', changed=True),
                    read('gap', changed=None), read('gap', tool='run_command'),
                    read('gap', status='failed')]
        for barrier in barriers:
            with self.subTest(barrier=barrier):
                observations = [read('A'), read('B')] * 2 + [barrier] + [read('A'), read('B')] * 2
                decisions = consume(observations)
                self.assertNotIn('pause', [item['action'] for item in decisions])
                self.assertEqual(decisions[-1]['action'], 'hint')

    def test_unknown_change_status_never_prompts_even_when_read_success_is_true(self):
        observations = [{key: value for key, value in read().items() if key != 'changed'}] * 20
        self.assertTrue(all(item['action'] == 'none' for item in consume(observations)))

    def test_failure_commands_polling_and_edit_reversals_never_pause(self):
        for observations in (
            [read('A', tool='run_command', ok=False), read('B', tool='run_command', ok=False)] * 20,
            [read('A', tool='run_command'), read('B', tool='run_command')] * 20,
            [read('A', tool='edit_file', changed=True), read('B', tool='edit_file', changed=True)] * 20,
        ):
            with self.subTest(tool=observations[0]['tool'], ok=observations[0]['ok']):
                self.assertTrue(all(item['action'] == 'none' for item in consume(observations)))

    def test_incomplete_or_non_periodic_repetitions_do_not_trigger(self):
        sequences = [['A', 'B', 'A'], ['A', 'B', 'C', 'A', 'B'],
                     ['A', 'B', 'A', 'C'] * 4,
                     ['A', 'B', 'C', 'D'] * 4]
        for sequence in sequences:
            with self.subTest(sequence=sequence):
                self.assertTrue(all(item['action'] == 'none' for item in consume([read(item) for item in sequence])))

    def test_old_cycles_outside_window_cannot_fill_a_new_cycle(self):
        decisions = consume([read('A'), read('B')] * 2 +
                            [read(f'new-{index}') for index in range(20)] + [read('A'), read('B')])
        self.assertEqual(decisions[-1]['action'], 'none')
        self.assertLessEqual(len(decisions[-1]['observations']), 12)

    def test_observation_history_and_callers_dictionaries_are_not_mutated(self):
        original = read('A')
        previous = [original, read('B'), read('A')]
        snapshot = [dict(row) for row in previous]
        assess_progress(previous, read('B'))
        self.assertEqual(previous, snapshot)

    def test_malformed_history_is_a_barrier_not_a_removed_gap(self):
        previous = [read('A'), read('B')] * 2 + [None] + [read('A'), read('B'), read('A')]
        result = assess_progress(previous, read('B'))
        self.assertEqual(result['action'], 'hint')
        self.assertEqual(result['repeats'], 4)

    def test_missing_or_nonstring_digests_do_not_form_a_stable_cycle(self):
        for arguments, result in (('', 'same'), ('A', ''), (True, 'same'), ('A', 1)):
            with self.subTest(arguments=arguments, result=result):
                decisions = consume([read(arguments, result=result)] * 20)
                self.assertTrue(all(item['action'] == 'none' for item in decisions))


class ToolObservationCycleTests(unittest.TestCase):
    def setUp(self):
        from nailong.core.sessions import ProjectSessionStore
        from nailong.core.task_state import TaskStore
        from nailong.tools.execution import ToolExecutionContext
        from nailong.tools.files import FileSession
        from tools import build_tools
        self.project = tempfile.TemporaryDirectory()
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.project.cleanup)
        self.addCleanup(self.data.cleanup)
        self.root = Path(self.project.name).resolve()
        self.tasks = TaskStore(ProjectSessionStore(self.root, base_dir=self.data.name))
        self.tasks.begin('owner', '调查本地源文件')
        self.decisions = []

        def observe(observation):
            progress = {key: observation[key] for key in
                ('arguments_digest', 'result_digest', 'input_version', 'ok', 'changed')}
            progress['tool'] = observation['name']
            self.decisions.append(self.tasks.observe_progress(observation['thread_id'], progress))
        execution = ToolExecutionContext(self.root, observer=observe)
        self.tools = {tool.name: tool for tool in build_tools(file_session=FileSession(self.root),
            execution_context=execution)}

    def call(self, arguments, index):
        message = self.tools['read_file'].invoke({'type': 'tool_call', 'name': 'read_file',
            'args': arguments, 'id': f'read-{index}'}, config={'configurable': {'thread_id': 'owner'}})
        self.assertTrue(json.loads(message.content)['ok'])

    def test_real_two_file_read_observations_pause_task_before_final_tool_returns(self):
        (self.root / 'a.py').write_text('alpha\n', newline='\n')
        (self.root / 'b.py').write_text('beta\n', newline='\n')
        for index, path in enumerate(['a.py', 'b.py'] * 4):
            self.call({'path': path}, index)
            if index < 7:
                self.assertEqual(self.tasks.snapshot('owner')['lifecycle'], 'active')
        self.assertEqual(self.decisions[3]['action'], 'hint')
        self.assertEqual(self.tasks.snapshot('owner')['lifecycle'], 'paused')
        self.assertEqual(self.decisions[-1]['repeats'], 8)

    def test_real_new_pages_keep_task_active(self):
        (self.root / 'pages.py').write_text('line\n' * 30, newline='\n')
        for index in range(20):
            self.call({'path': 'pages.py', 'offset': index + 1, 'limit': 1}, index)
        self.assertEqual(self.tasks.snapshot('owner')['lifecycle'], 'active')
        self.assertTrue(all(item['action'] == 'none' for item in self.decisions))


if __name__ == '__main__':
    unittest.main()
