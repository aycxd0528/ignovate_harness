"""Persisted runtime continuation is distinct from exact historical forks."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph

from nailong.core.branches import BranchStore
from nailong.core.sessions import ProjectSessionStore


class HistoryContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'project'
        self.project.mkdir()
        self.sessions = ProjectSessionStore(self.project, base_dir=Path(self.temp.name) / 'data')
        self.store = BranchStore(self.sessions)
        self.addCleanup(lambda: self.store.close())
        self.controller = SimpleNamespace(sessions=self.sessions, branches=self.store, factory=None)

    def graph_turn(self, thread='saved', *, prompt=None, answer=None, interrupt_before=None, turn_id=None,
                   checkpoint_timestamp='2026-10-16T08:00:05.500000+00:00'):
        prompt = prompt or HumanMessage('saved prompt')
        answer = answer or AIMessage('saved answer')
        with SqliteSaver.from_conn_string(str(self.sessions.database_path)) as saver:
            builder = StateGraph(MessagesState)
            builder.add_node('answer', lambda _: {'messages': [answer]})
            builder.add_edge(START, 'answer')
            builder.add_edge('answer', END)
            graph = builder.compile(checkpointer=saver, interrupt_before=interrupt_before)
            config = {'configurable': {'thread_id': thread}}
            if turn_id:
                config['metadata'] = {'nailong_turn_id': turn_id}
            graph.invoke({'messages': [prompt]}, config)
            saved = saver.get_tuple(config)
            # Keep the real graph state, but persist an independently chosen
            # fixture clock so slow test runs cannot move logs across seconds.
            if checkpoint_timestamp is None:
                saved.checkpoint.pop('ts', None)
            else:
                saved.checkpoint['ts'] = checkpoint_timestamp
            saver.put(saved.parent_config or config, saved.checkpoint, saved.metadata, {})
            return saved.checkpoint['id']

    def import_log(self, thread='saved', *, prompt='saved prompt', answer='saved answer', final=None,
                   user_timestamp='2026-10-16T08:00:05+00:00',
                   final_timestamp='2026-10-16T08:00:05+00:00', turn_id=None):
        if turn_id:
            self.sessions.append_event(thread, 'turn_start', {'turn_id': turn_id})
        self.sessions.append_event(thread, 'user', {'text': prompt}, timestamp=user_timestamp)
        self.sessions.append_event(thread, 'final', {'text': answer, **(final or {})}, timestamp=final_timestamp)
        if user_timestamp is None or final_timestamp is None:
            events = self.sessions.read_events(thread)
            for event, timestamp in zip(events[-2:], (user_timestamp, final_timestamp)):
                if timestamp is None:
                    event.pop('timestamp', None)
            self.sessions.session_path(thread).write_text(
                ''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')
        self.store.import_sessions()
        return next(branch for branch in self.store.branches() if branch['thread_id'] == thread)

    def state(self, branch):
        self.assertIsNotNone(importlib.util.find_spec('nailong.gui.history'),
                             'Persisted history continuation readiness is missing.')
        from nailong.gui.history import continuation_state
        return continuation_state(self.controller, branch)

    def test_completed_import_continues_after_restart_without_certifying_historical_fork(self):
        checkpoint = self.graph_turn()
        branch = self.import_log()
        original_head = branch['head']
        self.store.close()
        self.store = BranchStore(self.sessions)
        self.controller.branches = self.store
        result = self.state(branch)
        self.assertTrue(result['allowed'], result['reason'])
        self.assertEqual(result['checkpoint_id'], checkpoint)
        self.assertEqual(self.store.branch(branch['id'])['head'], original_head)
        self.assertTrue(self.store.branch(branch['id'])['legacy'])
        self.assertIsNone(self.store.node(original_head)['checkpoint_id'])
        self.assertFalse(self.store.path(branch['id'])[-1]['recoverable'])

    def test_log_without_persisted_runtime_cannot_continue(self):
        result = self.state(self.import_log())
        self.assertFalse(result['allowed'])
        self.assertIsNone(result['checkpoint_id'])
        self.assertIn('checkpoint', result['reason'])

    def test_checkpoint_must_match_both_latest_visible_input_and_final(self):
        for thread, prompt, answer in [('input', 'changed prompt', 'saved answer'),
                                       ('output', 'saved prompt', 'changed answer')]:
            with self.subTest(thread=thread):
                self.graph_turn(thread)
                result = self.state(self.import_log(thread, prompt=prompt, answer=answer))
                self.assertFalse(result['allowed'])
                self.assertIsNone(result['checkpoint_id'])
                self.assertTrue(result['reason'])

    def test_new_unfinished_cli_turn_cannot_reuse_previous_completed_checkpoint(self):
        self.graph_turn()
        branch = self.import_log()
        self.sessions.append_event('saved', 'turn_start', {'visible': True})
        result = self.state(branch)
        self.assertFalse(result['allowed'])
        self.assertIsNone(result['checkpoint_id'])

    def test_cli_rewrite_of_owned_history_is_rejected(self):
        self.graph_turn()
        branch = self.import_log()
        self.sessions.truncate_events('saved', 0)
        self.store.import_sessions()
        result = self.state(branch)
        self.assertFalse(result['allowed'])
        self.assertTrue(result['reason'])

    def test_pending_writes_are_not_a_completed_boundary(self):
        checkpoint = self.graph_turn()
        branch = self.import_log()
        with SqliteSaver.from_conn_string(str(self.sessions.database_path)) as saver:
            saver.put_writes({'configurable': {'thread_id': 'saved', 'checkpoint_ns': '',
                                              'checkpoint_id': checkpoint}},
                             [('__error__', 'unfinished')], 'pending-task')
        result = self.state(branch)
        self.assertFalse(result['allowed'])
        self.assertIsNone(result['checkpoint_id'])

    def test_interrupt_with_none_branch_channel_cannot_continue_as_completed(self):
        self.graph_turn(interrupt_before=['answer'])
        result = self.state(self.import_log())
        self.assertFalse(result['allowed'])
        self.assertIsNone(result['checkpoint_id'])

    def test_terminal_graph_with_unclosed_tool_exchange_is_rejected(self):
        self.graph_turn(answer=AIMessage('saved answer', tool_calls=[
            {'id': 'open-tool', 'name': 'read_file', 'args': {}}]))
        result = self.state(self.import_log())
        self.assertFalse(result['allowed'])
        self.assertIsNone(result['checkpoint_id'])

    def test_truncated_final_is_not_certified_as_completed(self):
        self.graph_turn()
        result = self.state(self.import_log(final={'output_status': 'truncated'}))
        self.assertFalse(result['allowed'])

    def test_display_metadata_and_log_redaction_limits_match_the_saved_runtime(self):
        self.sessions.api_key = 'test-secret'
        self.graph_turn(prompt=HumanMessage('internal execution instructions', additional_kwargs={
            'nailong_display': 'visible test-secret request'}), answer=AIMessage('x' * 13000))
        result = self.state(self.import_log(prompt='visible test-secret request', answer='x' * 13000))
        self.assertTrue(result['allowed'], result['reason'])

    def test_factory_sqlite_saver_can_supply_the_saved_runtime(self):
        self.graph_turn()
        branch = self.import_log()
        with SqliteSaver.from_conn_string(str(self.sessions.database_path)) as saver:
            self.controller.factory = SimpleNamespace(checkpointer=saver)
            result = self.state(branch)
        self.assertTrue(result['allowed'], result['reason'])

    def test_fresh_gui_branch_does_not_require_a_checkpoint(self):
        branch = self.store.create('new GUI conversation')
        result = self.state(branch)
        self.assertTrue(result['allowed'], result['reason'])
        self.assertIsNone(result['checkpoint_id'])

    def test_native_gui_branch_can_queue_while_another_project_run_holds_the_lease(self):
        from nailong.core.file_locks import file_lock
        branch = self.store.create('new GUI conversation')
        with file_lock(self.sessions.root / 'project-execution.lock', blocking=False):
            result = self.state(branch)
        self.assertTrue(result['allowed'], result['reason'])

    def test_imported_branch_waits_while_another_project_run_holds_the_lease(self):
        from nailong.core.file_locks import file_lock
        self.graph_turn()
        branch = self.import_log()
        with file_lock(self.sessions.root / 'project-execution.lock', blocking=False):
            result = self.state(branch)
        self.assertFalse(result['allowed'])
        self.assertIsNone(result['checkpoint_id'])
        self.assertIn('占用', result['reason'])

    def test_identical_text_from_a_different_recorded_turn_is_rejected(self):
        self.graph_turn(turn_id='checkpoint-turn')
        self.sessions.append_event('saved', 'turn_start', {'turn_id': 'different-log-turn'})
        result = self.state(self.import_log())
        self.assertFalse(result['allowed'])
        self.assertIsNone(result['checkpoint_id'])

    def test_corrupted_checkpoint_database_returns_a_safe_refusal(self):
        branch = self.import_log()
        self.sessions.database_path.write_bytes(b'invalid sqlite database')
        result = self.state(branch)
        self.assertFalse(result['allowed'])
        self.assertIsNone(result['checkpoint_id'])
        self.assertIn('checkpoint', result['reason'])

    def test_repeated_identical_next_day_log_cannot_reuse_previous_checkpoint(self):
        self.graph_turn()
        self.import_log()
        branch = self.import_log(user_timestamp='2026-10-17T08:00:05+00:00',
                                 final_timestamp='2026-10-17T08:00:05+00:00')
        result = self.state(branch)
        self.assertFalse(result['allowed'])
        self.assertIsNone(result['checkpoint_id'])
        self.assertIn('时间', result['reason'])

    def test_checkpoint_time_uses_inclusive_input_floor_and_exclusive_final_second(self):
        for thread, timestamp, allowed in [
                ('before-input', '2026-10-16T08:00:04.999999+00:00', False),
                ('at-input', '2026-10-16T08:00:05.000000+00:00', True),
                ('before-end', '2026-10-16T08:00:05.999999+00:00', True),
                ('at-end', '2026-10-16T08:00:06.000000+00:00', False)]:
            with self.subTest(thread=thread):
                self.graph_turn(thread, checkpoint_timestamp=timestamp)
                result = self.state(self.import_log(thread))
                self.assertEqual(result['allowed'], allowed, result['reason'])
                if not allowed:
                    self.assertIsNone(result['checkpoint_id'])
                    self.assertIn('时间', result['reason'])

    def test_missing_invalid_or_unzoned_times_require_a_matching_stable_turn(self):
        cases = [('missing-checkpoint', {'checkpoint_timestamp': None}, {}),
                 ('invalid-checkpoint', {'checkpoint_timestamp': 'invalid'}, {}),
                 ('missing-user', {}, {'user_timestamp': None}),
                 ('missing-final', {}, {'final_timestamp': None}),
                 ('naive-user', {}, {'user_timestamp': '2026-10-16T08:00:05'}),
                 ('invalid-final', {}, {'final_timestamp': 'invalid'})]
        for thread, checkpoint, log in cases:
            with self.subTest(thread=thread):
                self.graph_turn(thread, **checkpoint)
                result = self.state(self.import_log(thread, **log))
                self.assertFalse(result['allowed'])
                self.assertIsNone(result['checkpoint_id'])
                self.assertIn('缺少可核对的时间', result['reason'])

    def test_matching_stable_turn_can_replace_unavailable_timestamps(self):
        self.graph_turn(turn_id='same-turn', checkpoint_timestamp=None)
        result = self.state(self.import_log(turn_id='same-turn', user_timestamp=None, final_timestamp='invalid'))
        self.assertTrue(result['allowed'], result['reason'])

    def test_matching_turn_does_not_override_present_but_inconsistent_times(self):
        self.graph_turn(turn_id='same-turn')
        result = self.state(self.import_log(turn_id='same-turn',
            user_timestamp='2026-10-17T08:00:05+00:00', final_timestamp='2026-10-17T08:00:05+00:00'))
        self.assertFalse(result['allowed'])
        self.assertIn('时间', result['reason'])

    def test_one_sided_turn_identity_cannot_replace_missing_timestamps(self):
        for thread, checkpoint_turn, log_turn in [('only-checkpoint', 'same-turn', None),
                                                  ('only-log', None, 'same-turn')]:
            with self.subTest(thread=thread):
                self.graph_turn(thread, turn_id=checkpoint_turn, checkpoint_timestamp=None)
                result = self.state(self.import_log(thread, turn_id=log_turn))
                self.assertFalse(result['allowed'])
                self.assertIn('缺少可核对的时间', result['reason'])

    def test_previous_turn_identity_cannot_certify_a_later_unidentified_log_turn(self):
        self.graph_turn(turn_id='previous-turn')
        self.import_log(turn_id='previous-turn')
        result = self.state(self.import_log(user_timestamp=None, final_timestamp=None))
        self.assertFalse(result['allowed'])
        self.assertIn('缺少可核对的时间', result['reason'])

    def test_equivalent_timezone_offsets_match_the_same_checkpoint_second(self):
        self.graph_turn()
        result = self.state(self.import_log(user_timestamp='2026-10-16T16:00:05+08:00',
                                           final_timestamp='2026-10-16T16:00:05+08:00'))
        self.assertTrue(result['allowed'], result['reason'])

    def test_final_answer_whitespace_is_normalized_like_the_service(self):
        self.graph_turn(answer=AIMessage('  saved answer\n'))
        result = self.state(self.import_log(answer='saved answer'))
        self.assertTrue(result['allowed'], result['reason'])

    def test_empty_final_answer_matches_the_service_placeholder(self):
        for thread, content in [('empty', ''), ('whitespace', ' \n\t')]:
            with self.subTest(thread=thread):
                self.graph_turn(thread, answer=AIMessage(content))
                result = self.state(self.import_log(thread, answer='模型返回了空回答。'))
                self.assertTrue(result['allowed'], result['reason'])


if __name__ == '__main__':
    unittest.main()
