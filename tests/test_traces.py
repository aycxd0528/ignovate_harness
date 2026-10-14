from platform_fixtures import assert_private
import importlib
import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from config import Settings
from nailong.core.permissions import PermissionEngine
from nailong.core.sessions import ProjectSessionStore
from ui.actions import CommandActions
from ui.controller import CommandController


class TraceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='nailong-trace-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve() / 'project'
        self.root.mkdir()
        self.store = ProjectSessionStore(self.root, base_dir=Path(directory.name) / 'data')

    def traces(self):
        self.assertIsNotNone(importlib.util.find_spec('nailong.core.traces'),
                             '缺少可观察轨迹查看与安全导出实现')
        return importlib.import_module('nailong.core.traces').TraceActions(
            self.root, self.store, api_key='private-key')

    def event(self, kind, **data):
        self.store.append_event('one', kind, data, timestamp='2026-10-05T10:00:00+00:00')

    async def approve(self, action, index, total):
        return 'approve_once'

    def test_latest_turn_has_stable_sequences_and_ids_when_appended(self):
        self.event('turn_start', profile='chat')
        self.event('user', text='old turn')
        self.event('final', text='old answer')
        self.event('turn_start', profile='review', visible=False)
        self.event('model_call', model_call=1, model_call_limit=8)
        first = self.traces().latest('one')
        self.assertEqual([row['sequence'] for row in first['events']], [4, 5])
        self.assertEqual(first['run_id_source'], 'derived')
        self.assertEqual(first['state'], 'incomplete')
        self.event('final', text='done')
        second = self.traces().latest('one')
        self.assertEqual(first['run_id'], second['run_id'])
        self.assertEqual(first['events'], second['events'][:2])
        self.assertEqual(second['state'], 'complete')
        self.assertEqual(second['events'][0]['data']['visible'], False)

    def test_missing_boundary_remains_unknown_and_empty_is_read_only(self):
        before = list(self.root.iterdir())
        empty = self.traces().latest('empty')
        self.assertEqual(empty['events'], [])
        self.assertIsNone(empty['run_id'])
        self.assertEqual(list(self.root.iterdir()), before)
        self.event('usage', input_tokens=0)
        report = self.traces().latest('one')
        self.assertIsNone(report['run_id'])
        self.assertEqual(report['boundary'], 'unknown')
        self.assertEqual(report['state'], 'unknown')

    def test_parent_requires_unique_matching_tool_call_and_name(self):
        self.event('turn_start', profile='chat')
        self.event('tool_start', name='read_file', call_id='read-1')
        self.event('tool_end', name='read_file', call_id='read-1', ok=True)
        self.event('tool_end', name='write_file', call_id='read-1', ok=True)
        self.event('tool_end', name='read_file', ok=True)
        self.event('model_usage_call', scope='subagent', input_tokens=5, output_tokens=2)
        rows = self.traces().latest('one')['events']
        self.assertEqual(rows[2]['parent_event_id'], rows[1]['event_id'])
        self.assertTrue(all(row['parent_event_id'] is None for row in rows[3:]))
        self.event('tool_start', name='read_file', call_id='read-1')
        self.event('tool_end', name='read_file', call_id='read-1')
        duplicate = self.traces().latest('one')['events']
        self.assertIsNone(duplicate[-1]['parent_event_id'])
        self.assertIsNone(duplicate[2]['parent_event_id'])

    def test_matching_call_id_with_different_scope_does_not_prove_parent(self):
        self.event('turn_start', profile='chat')
        self.event('tool_start', name='read_file', call_id='c1', scope='main')
        self.event('tool_end', name='read_file', call_id='c1', scope='subagent')
        self.assertIsNone(self.traces().latest('one')['events'][-1]['parent_event_id'])

    def test_metadata_control_character_cleanup_cannot_recreate_secret(self):
        self.event('turn_start', profile='chat')
        self.event('usage', model='private-\nkey', provider_host='private-\x1bkey')
        report = self.traces().latest('one')
        encoded = json.dumps(report, ensure_ascii=False)
        self.assertNotIn('private-key', encoded)
        self.assertEqual(report['events'][-1]['data']['model'], '[密钥已隐藏]')

    def test_raw_content_and_unknown_fields_are_omitted_and_key_redacted(self):
        self.event('turn_start', profile='chat', message_ids=['private-message'])
        self.event('user', text='RAW_USER_SECRET')
        self.event('token', text='RAW_TOKEN_SECRET', reasoning='PRIVATE_THOUGHT')
        self.event('tool_start', name='run_command', call_id='c1',
                   args={'command': 'RAW_COMMAND_SECRET'}, preview={'command': 'RAW_COMMAND_SECRET'})
        self.event('tool_end', name='run_command', call_id='c1', ok=False,
                   summary='RAW_ERROR_SECRET', output_preview='RAW_OUTPUT_SECRET', diff='RAW_DIFF_SECRET')
        self.event('usage', model='model-private-key', input_tokens=0, output_tokens=2)
        self.event('final', text='RAW_FINAL_SECRET', delivery={'status': 'unverified', 'reasons': ['RAW_REASON_SECRET']})
        self.event('future_event', raw='RAW_UNKNOWN_SECRET')
        report = self.traces().latest('one')
        encoded = json.dumps(report, ensure_ascii=False)
        for text in ('RAW_', 'PRIVATE_THOUGHT', 'private-message', 'private-key'):
            self.assertNotIn(text, encoded)
        self.assertIn('[密钥已隐藏]', encoded)
        self.assertEqual(report['events'][4]['data']['ok'], False)
        self.assertEqual(report['events'][6]['data']['delivery_status'], 'unverified')
        self.assertTrue(report['events'][2]['data']['content_omitted'])

    def test_usage_preserves_zero_unknown_and_recorded_artifact_references(self):
        self.event('turn_start', profile='chat')
        self.event('usage', scope='main', input_tokens=0, output_tokens=2, model='recorded-model')
        self.event('usage_missing', scope='subagent', reason='request_failed')
        self.event('review_coverage', status='reviewed', coverage='complete',
                   evidence={'artifact_ref': 'events:one:review_coverage', 'summary': 'RAW_EVIDENCE'})
        self.event('tool_end', name='read_tool_result', artifact_ref='tool:actual-ref')
        rows = self.traces().latest('one')['events']
        self.assertEqual(rows[1]['data']['input_tokens'], 0)
        self.assertIsNone(rows[1]['data']['total_tokens'])
        self.assertEqual(rows[2]['data']['reason'], 'request_failed')
        self.assertEqual(rows[3]['data']['artifact_refs'], ['events:one:review_coverage'])
        self.assertEqual(rows[4]['data']['artifact_refs'], ['tool:actual-ref'])
        self.assertNotIn('RAW_EVIDENCE', json.dumps(rows))

    async def test_budget_refusal_diagnostics_survive_trace_export_without_raw_body(self):
        self.event('turn_start', profile='chat')
        self.event('subagent_budget', event='refused', task_key='0123456789abcdef',
                   limit_tokens=30000, spent_tokens=110, reserved_tokens=5000,
                   remaining_tokens=24890, input_tokens_upper_bound=None,
                   input_method='prompt_only_estimate', admission_tokens_estimate=31000,
                   output_tokens_requested=1500, output_tokens_reserved=0,
                   reservation_tokens=0, charged_tokens=0, usage_complete=False,
                   reason='admission_does_not_fit', prompt='RAW_PROMPT',
                   args={'task': 'RAW_ARGS'}, error='RAW_ERROR')
        actions = self.traces()
        report = actions.latest('one')
        data = report['events'][-1]['data']
        expected = {'event': 'refused', 'task_key': '0123456789abcdef',
                    'limit_tokens': 30000, 'spent_tokens': 110, 'reserved_tokens': 5000,
                    'remaining_tokens': 24890, 'input_tokens_upper_bound': None,
                    'input_method': 'prompt_only_estimate', 'admission_tokens_estimate': 31000,
                    'output_tokens_requested': 1500, 'output_tokens_reserved': 0,
                    'reservation_tokens': 0, 'charged_tokens': 0, 'usage_complete': False,
                    'reason': 'admission_does_not_fit'}
        self.assertEqual({key: data.get(key) for key in expected}, expected)
        self.assertIn('31000', actions.render(report))
        result = await actions.export('one', approval=self.approve)
        content = Path(result['path']).read_text()
        self.assertNotIn('RAW_', content)
        exported = json.loads(content.splitlines()[-1])['data']
        self.assertEqual({key: exported.get(key) for key in expected}, expected)

    def test_budget_unknown_values_remain_unknown_without_copying_text(self):
        self.event('turn_start', profile='chat')
        self.event('subagent_budget', event='RAW_STAGE', task_key='RAW_TASK',
                   reason='RAW_ERROR_BODY', input_method='RAW_METHOD',
                   limit_tokens=True, spent_tokens=-1, remaining_tokens='RAW_BALANCE',
                   reserved_tokens=1.5, output_tokens_reserved=0)
        data = self.traces().latest('one')['events'][-1]['data']
        self.assertEqual(data.get('output_tokens_reserved'), 0)
        for key in ('event', 'task_key', 'reason', 'input_method', 'limit_tokens',
                    'spent_tokens', 'remaining_tokens', 'reserved_tokens'):
            self.assertIsNone(data[key])
        self.assertNotIn('RAW_', json.dumps(data))

    async def test_real_budget_reservation_and_settlement_are_observable_without_model_api(self):
        from langchain_core.messages import AIMessage, HumanMessage
        from nailong.core.budgets import active_token_budget
        from nailong.tools.agents import ReadOnlyTaskRunner

        self.event('turn_start', profile='chat')
        async def worker(prompt):
            budget = active_token_budget.get()
            request = SimpleNamespace(messages=[HumanMessage(content=prompt)], system_message=None,
                                      tools=[], model=SimpleNamespace(max_tokens=200), model_settings={})
            reservation, _ = await budget.reserve(request)
            await budget.settle(reservation, [AIMessage(content='RAW_TOOL_RESULT', usage_metadata={
                'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12})])
            return 'RAW_TOOL_RESULT'
        runner = ReadOnlyTaskRunner(worker, token_budget=10000, child_output_tokens=200,
                                    record=lambda thread_id, row: self.store.append_event(thread_id, 'subagent_budget', row))
        await runner.run('one', 'RAW_PROMPT_SECRET')
        report = self.traces().latest('one')
        rows = [row['data'] for row in report['events'] if row['kind'] == 'subagent_budget']
        self.assertEqual([row.get('event') for row in rows], ['reserved', 'settled'])
        self.assertEqual(rows[0]['input_method'], 'utf8_upper_bound')
        self.assertGreater(rows[0]['input_tokens_upper_bound'], 1024)
        self.assertEqual(rows[0]['output_tokens_reserved'], 200)
        self.assertEqual(len(rows[0]['task_key']), 16)
        self.assertEqual(rows[1]['spent_tokens'], 12)
        self.assertEqual(rows[1]['remaining_tokens'], 9988)
        self.assertEqual(rows[1]['reserved_tokens'], 0)
        self.assertEqual(rows[1]['charged_tokens'], 12)
        self.assertTrue(rows[1]['usage_complete'])
        self.assertNotIn('RAW_', json.dumps(report))

    async def test_full_permissions_keep_trace_export_project_scoped(self):
        outside = self.root.parent / 'external-trace.jsonl'
        with self.assertRaises(ValueError):
            await self.traces().export('one', str(outside), permission_mode='bypassPermissions')
        self.assertFalse(outside.exists())
        result = await self.traces().export('one', 'trace.jsonl', permission_mode='bypassPermissions')
        self.assertTrue(result['written'])

    def test_recorded_run_id_and_safe_bounded_rendering(self):
        self.event('turn_start', profile='chat', run_id='actual-run')
        for index in range(65):
            self.event('model_call', model_call=index + 1)
        actions = self.traces()
        report = actions.latest('one')
        self.assertEqual(report['run_id'], 'actual-run')
        self.assertEqual(report['run_id_source'], 'recorded')
        text = actions.render(report)
        self.assertIn('私有', text)
        self.assertIn('66', text)
        self.assertIn('50', text)
        self.assertIn('66.', text)
        self.assertNotIn('\n1.', text)

    def test_retained_rewound_usage_has_no_current_run_attribution(self):
        self.event('turn_start', profile='chat')
        self.event('final', text='done')
        self.event('usage', input_tokens=99, output_tokens=10, rewound=True)
        report = self.traces().latest('one')
        self.assertIsNone(report['events'][-1]['run_id'])
        self.assertTrue(report['events'][-1]['data']['rewound'])

    def test_malformed_metadata_and_journal_lines_do_not_invent_values(self):
        self.event('turn_start', profile='chat')
        self.event('usage', input_tokens=True, output_tokens=-2, total_tokens='unknown')
        with self.store.session_path('one').open('a') as stream:
            stream.write('{incomplete\n')
        report = self.traces().latest('one')
        self.assertEqual(len(report['events']), 2)
        self.assertIsNone(report['events'][-1]['data']['input_tokens'])
        self.assertIsNone(report['events'][-1]['data']['output_tokens'])
        self.assertIsNone(report['events'][-1]['data']['total_tokens'])

    def test_large_numeric_metadata_does_not_crash_projection(self):
        self.event('turn_start', profile='chat')
        self.event('usage', input_tokens=10 ** 400)
        self.assertIsNone(self.traces().latest('one')['events'][-1]['data']['input_tokens'])

    async def test_deny_rule_and_changed_approval_policy_leave_destination_untouched(self):
        settings = self.root / '.nailong/settings.json'
        settings.parent.mkdir()
        settings.write_text(json.dumps({'permissions': {'deny': ['Write(trace.jsonl)']}}), newline='\n')
        engine = PermissionEngine(self.root)
        result = await self.traces().export('one', 'trace.jsonl', approval=self.approve,
                                           permission_engine=engine)
        self.assertFalse(result['written'])
        self.assertFalse((self.root / 'trace.jsonl').exists())
        settings.write_text('{}', newline='\n')
        engine = PermissionEngine(self.root)
        async def change_policy(action, index, total):
            engine.add_rule('deny', 'Write(trace.jsonl)')
            return 'approve_once'
        result = await self.traces().export('one', 'trace.jsonl', approval=change_policy,
                                           permission_engine=engine)
        self.assertFalse(result['written'])
        self.assertFalse((self.root / 'trace.jsonl').exists())

    async def test_default_ask_without_approval_writes_nothing(self):
        self.event('turn_start', profile='chat')
        result = await self.traces().export('one')
        self.assertFalse(result['written'])
        self.assertFalse((self.root / '.nailong').exists())

    async def test_approved_export_has_manifest_jsonl_and_same_ids(self):
        self.event('turn_start', profile='chat')
        self.event('tool_end', name='read_file', ok=False, summary='RAW_ERROR_SECRET')
        self.event('final', text='RAW_FINAL_SECRET', stats={'model_calls': 2, 'tool_calls': 1})
        decisions = []
        async def approve(action, index, total):
            decisions.append(action)
            return 'approve_once'
        actions = self.traces()
        report = actions.latest('one')
        result = await actions.export('one', approval=approve)
        self.assertTrue(result['written'])
        path = Path(result['path'])
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(lines[0]['record_type'], 'trace_manifest')
        self.assertEqual(lines[0]['run_id'], report['run_id'])
        self.assertEqual([row['event_id'] for row in lines[1:]],
                         [row['event_id'] for row in report['events']])
        self.assertNotIn('RAW_', path.read_text())
        assert_private(self, path)
        self.assertEqual(decisions[0]['name'], 'write_file')
        self.assertEqual(decisions[0]['preview']['event_count'], 3)
        self.assertIn('trace_manifest', decisions[0]['preview']['diff'])
        self.assertNotIn('RAW_', decisions[0]['preview']['diff'])
        self.assertFalse(list(path.parent.glob('.trace-*')))

    async def test_export_path_containing_configured_key_never_reaches_approval(self):
        asked = []
        async def approve(action, index, total):
            asked.append(action)
            return 'approve_once'
        with self.assertRaises(ValueError) as error:
            await self.traces().export('one', 'private-key.jsonl', approval=approve)
        self.assertNotIn('private-key', str(error.exception))
        self.assertFalse(asked)
        self.assertFalse((self.root / 'private-key.jsonl').exists())

    async def test_permission_allow_and_plan_deny_apply_to_new_export(self):
        self.event('turn_start', profile='chat')
        allowed = await self.traces().export('one', 'allowed.jsonl', permission_mode='acceptEdits')
        self.assertTrue(allowed['written'])
        denied = await self.traces().export('one', 'blocked/new.jsonl',
                                           permission_mode='plan', approval=self.approve)
        self.assertFalse(denied['written'])
        self.assertFalse((self.root / 'blocked').exists())

    async def test_paths_reject_escape_protected_and_symlink_before_write(self):
        self.event('turn_start', profile='chat')
        outside = self.root.parent / 'outside'
        outside.mkdir()
        (self.root / 'link').symlink_to(outside, target_is_directory=True)
        for path in ('../escaped.jsonl', '.env', '.git/trace.jsonl', 'link/trace.jsonl'):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    await self.traces().export('one', path, approval=self.approve)
        self.assertFalse((outside / 'trace.jsonl').exists())
        self.assertFalse((self.root.parent / 'escaped.jsonl').exists())

    async def test_rejected_overwrite_preserves_original_bytes(self):
        destination = self.root / 'trace.jsonl'
        original = b'keep\x00unchanged\xff'
        destination.write_bytes(original)
        result = await self.traces().export('one', str(destination))
        self.assertFalse(result['written'])
        self.assertEqual(destination.read_bytes(), original)

    async def test_target_changed_during_approval_is_not_overwritten(self):
        destination = self.root / 'trace.jsonl'
        destination.write_text('original', newline='\n')
        async def change(action, index, total):
            destination.write_text('concurrent change', newline='\n')
            return 'approve_once'
        with self.assertRaises(ValueError):
            await self.traces().export('one', str(destination), approval=change)
        self.assertEqual(destination.read_text(), 'concurrent change')
        self.assertFalse(list(self.root.glob('.trace-*')))

    async def test_parent_symlink_change_during_approval_is_rejected(self):
        parent = self.root / 'export'
        parent.mkdir()
        outside = self.root.parent / 'outside'
        outside.mkdir()
        async def redirect(action, index, total):
            parent.rmdir()
            parent.symlink_to(outside, target_is_directory=True)
            return 'approve_once'
        with self.assertRaises(ValueError):
            await self.traces().export('one', 'export/trace.jsonl', approval=redirect)
        self.assertFalse(list(outside.iterdir()))

    async def test_cli_routes_locally_and_rejects_unknown_arguments(self):
        self.event('turn_start', profile='chat')
        settings = Settings('private-key', 'https://api.invalid', 'fixture-model', self.root)
        controller = CommandController(settings)
        actions = CommandActions(controller)
        service = SimpleNamespace(runtime_factory=None, session_store=self.store,
                                  permission_engine=PermissionEngine(self.root), permission_mode='default')
        request = controller.resolve('/trace')
        self.assertEqual(request.kind, 'local')
        self.assertTrue(actions.handles(request))
        result = await actions.execute(request, service, 'one')
        self.assertFalse(result.model_requests)
        self.assertEqual(result.data['trace']['events'][0]['kind'], 'turn_start')
        exported = await actions.execute(controller.resolve('/trace export "a file.jsonl"'),
                                         service, 'one', approval=self.approve)
        self.assertTrue(exported.data['written'])
        self.assertTrue((self.root / 'a file.jsonl').exists())
        for command in ('/trace unknown', '/trace export a b'):
            with self.assertRaises(ValueError):
                await actions.execute(controller.resolve(command), service, 'one')
        service.session_store = None
        with self.assertRaises(ValueError):
            await actions.execute(request, service, 'one')


if __name__ == '__main__':
    unittest.main()
