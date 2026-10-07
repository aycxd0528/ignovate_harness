"""Local execution-boundary contracts; no model/provider calls are made."""

import asyncio
import json
import hashlib
import os
import shlex
import shutil
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from langgraph.errors import GraphInterrupt

import local_tools
from nailong.core.permissions import PermissionEngine
from nailong.tools.coordination import active_file_version
from nailong.tools.execution import ToolExecutionContext, current_tool_execution
from nailong.tools.files import FileSession
from nailong.tools.registry import build_tool_specs
from nailong.tools.results import ResultArchive
from tools import build_tools


class Workspace:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.session = FileSession(self.root)
        (self.root / 'sample.txt').write_text('before\nsecond\n', encoding='utf-8')
        self.observations = []
        self.engine = PermissionEngine(self.root, rules={})
        self.execution = ToolExecutionContext(self.root, permission_engine=self.engine,
            observer=self.observations.append, artifact_directory=self.root / 'artifacts')
        self.tools = self.make_tools()

    def make_tools(self, profile='chat', **kwargs):
        return {tool.name: tool for tool in build_tools(profile=profile,
            file_session=self.session, execution_context=self.execution, **kwargs)}

    def call(self, name, arguments, *, tools=None, call_id='call-1'):
        return (tools or self.tools)[name].invoke(
            {'type': 'tool_call', 'name': name, 'args': arguments, 'id': call_id},
            config={'configurable': {'thread_id': 'owner'}})

    async def acall(self, name, arguments, *, tools=None, call_id='call-1'):
        return await (tools or self.tools)[name].ainvoke(
            {'type': 'tool_call', 'name': name, 'args': arguments, 'id': call_id},
            config={'configurable': {'thread_id': 'owner'}})

    def body(self, message):
        return json.loads(message.content)

    def spy(self, name, tools=None):
        tool = (tools or self.tools)[name]
        original = tool.spec.handler
        entered = []

        def handler(**kwargs):
            entered.append(kwargs)
            return original(**kwargs)

        tool.spec = replace(tool.spec, handler=handler)
        return entered


class ManagedSyncToolsTests(Workspace, unittest.TestCase):
    def test_observation_hashes_canonical_arguments_without_persisting_plaintext(self):
        self.tools['grep'].spec = replace(self.tools['grep'].spec,
            handler=lambda **kwargs: {'ok': True, 'content': 'private-result-sentinel', 'version': 'v1'})
        message = self.call('grep', {'pattern': 'private-argument-sentinel'}, call_id='hash-call')
        self.assertTrue(self.body(message)['ok'])
        observation = self.observations[-1]
        self.assertEqual(observation['arguments_digest'], hashlib.sha256(
            b'{"context":0,"include":null,"limit":100,"output_mode":"content","path":".","pattern":"private-argument-sentinel","pattern_mode":"regex"}').hexdigest())
        self.assertEqual(observation['result_digest'], hashlib.sha256(
            b'{"content":"private-result-sentinel","ok":true,"started":true,"status":"success","version":"v1"}').hexdigest())
        self.assertEqual(observation['input_version'], 'v1')
        self.assertIs(observation['ok'], True)
        self.assertIs(observation['changed'], False)
        encoded = json.dumps(observation)
        self.assertNotIn('private-argument-sentinel', encoded)
        self.assertNotIn('private-result-sentinel', encoded)
        self.assertNotIn('args', observation)
        self.assertNotIn('content', observation)
        self.assertNotIn('result', observation)

    def test_observation_result_hash_ignores_nested_transport_but_detects_business_change(self):
        payload = {'ok': True, 'content': 'business value', 'metadata': {'label': 'stable',
            'entries': [{'timestamp': 'first', 'number': 1}]}, 'version': 'v1'}
        self.tools['read_file'].spec = replace(self.tools['read_file'].spec, handler=lambda **kwargs: dict(payload))
        keys = ('elapsed_ms', 'duration_ms', 'duration', 'timestamp', 'started_at', 'finished_at',
                'call_id', 'tool_call_id', 'artifact_ref', 'result_ref', 'reference')
        for key in keys:
            payload[key] = 'first'
            payload['metadata'][key] = 'first'
        self.call('read_file', {'max_chars': 4, 'path': 'sample.txt'}, call_id='first-id')
        first = self.observations[-1]
        for key in keys:
            payload[key] = 'different transport value'
            payload['metadata'][key] = 'different transport value'
        payload['metadata']['entries'][0]['timestamp'] = 'second'
        self.call('read_file', {'path': 'sample.txt', 'max_chars': 4}, call_id='second-id')
        second = self.observations[-1]
        self.assertEqual(first['arguments_digest'], second['arguments_digest'])
        self.assertEqual(first['result_digest'], second['result_digest'])
        payload['content'] = 'new business value'
        self.call('read_file', {'path': 'sample.txt', 'max_chars': 4})
        self.assertNotEqual(second['result_digest'], self.observations[-1]['result_digest'])
        self.call('read_file', {'path': 'sample.txt', 'max_chars': 5})
        self.assertNotEqual(second['arguments_digest'], self.observations[-1]['arguments_digest'])

    def test_observation_hash_of_bounded_result_does_not_depend_on_new_archive_reference(self):
        (self.root / 'sample.txt').write_text('"' * 10000)
        first = self.body(self.call('read_file', {'path': 'sample.txt'}, call_id='first-id'))
        first_observation = self.observations[-1]
        second = self.body(self.call('read_file', {'path': 'sample.txt'}, call_id='second-id'))
        second_observation = self.observations[-1]
        self.assertNotEqual(first['reference'], second['reference'])
        self.assertEqual(first_observation['result_digest'], second_observation['result_digest'])
        self.assertEqual(first_observation['input_version'], first['version'])
        self.assertFalse(first['read_complete'])

    def test_observation_changed_and_ok_are_gate_owned_booleans(self):
        self.engine.add_rule('allow', 'Write(*)')
        self.call('read_file', {'path': 'sample.txt'})
        self.assertIs(self.observations[-1]['ok'], True)
        self.assertIs(self.observations[-1]['changed'], False)
        self.call('write_file', {'path': 'sample.txt', 'content': 'changed'})
        self.assertIs(self.observations[-1]['changed'], True)
        self.call('write_file', {'path': 'sample.txt', 'content': 'unread replacement'})
        self.assertIs(self.observations[-1]['ok'], False)
        self.assertIs(self.observations[-1]['changed'], False)
        self.assertEqual(self.observations[-1]['input_version'], '')
        self.tools['read_file'].spec = replace(self.tools['read_file'].spec,
            handler=lambda **kwargs: {'ok': True, 'digest': 'digest-v2', 'changed': True})
        self.call('read_file', {'path': 'sample.txt'})
        self.assertEqual(self.observations[-1]['input_version'], 'digest-v2')
        self.assertIs(self.observations[-1]['changed'], False)

    def test_denied_read_never_enters_handler_or_pre_hook(self):
        self.engine.add_rule('deny', 'Read(sample.txt)')
        entered = self.spy('read_file')
        hooks = []
        self.execution.pre_tool_hook = hooks.append
        message = self.call('read_file', {'path': 'sample.txt'})
        self.assertEqual(message.status, 'error')
        self.assertEqual(self.body(message)['error_code'], 'permission_denied')
        self.assertEqual(entered, [])
        self.assertEqual(hooks, [])
        self.assertFalse(self.observations[-1]['started'])
        self.assertEqual(self.observations[-1]['thread_id'], 'owner')
        self.assertEqual(self.observations[-1]['call_id'], 'call-1')

    def test_default_ask_fails_closed_without_approval_bridge(self):
        entered = self.spy('write_file')
        result = self.body(self.call('write_file', {'path': 'new.txt', 'content': 'new'}))
        self.assertEqual(result['error_code'], 'approval_required')
        self.assertFalse(result['started'])
        self.assertEqual(entered, [])
        self.assertFalse((self.root / 'new.txt').exists())

    def test_approved_new_file_uses_trusted_profile_before_hook(self):
        actions = []
        self.execution.approval_handler = lambda action, permission: {'type': 'approve'}
        self.execution.pre_tool_hook = actions.append
        result = self.body(self.call('write_file', {'path': 'new.txt', 'content': '中文'}))
        self.assertTrue(result['ok'])
        self.assertEqual((self.root / 'new.txt').read_text(), '中文')
        self.assertEqual(actions[0]['profile'], 'chat')
        self.assertEqual(actions[0]['file_version'], {'path': 'new.txt', 'exists': False})

    def test_unknown_or_rejected_approval_never_creates_file(self):
        entered = self.spy('write_file')
        for answer in (None, {}, 'yes', {'type': 'reject'}):
            with self.subTest(answer=answer):
                self.execution.approval_handler = lambda *args: answer
                result = self.body(self.call('write_file', {'path': 'new.txt', 'content': 'new'}))
                self.assertEqual(result['error_code'], 'approval_rejected')
        self.assertEqual(entered, [])
        self.assertFalse((self.root / 'new.txt').exists())

    def test_pre_hook_exception_is_unstarted_and_never_runs_post_hook(self):
        entered = self.spy('read_file')
        posts = []
        def broken(action):
            raise RuntimeError('hook failure')
        self.execution.pre_tool_hook = broken
        self.execution.post_tool_hook = lambda *args: posts.append(args)
        result = self.body(self.call('read_file', {'path': 'sample.txt'}))
        self.assertEqual(result['error_code'], 'execution_error')
        self.assertFalse(result['started'])
        self.assertEqual(entered, [])
        self.assertEqual(posts, [])

    def test_permission_changed_by_pre_hook_blocks_execution(self):
        self.execution.pre_tool_hook = lambda action: self.engine.add_rule('deny', 'Read(sample.txt)')
        entered = self.spy('read_file')
        result = self.body(self.call('read_file', {'path': 'sample.txt'}))
        self.assertEqual(result['error_code'], 'permission_denied')
        self.assertEqual(entered, [])

    def test_observer_failure_does_not_repeat_write(self):
        self.engine.add_rule('allow', 'Write(*)')
        entered = self.spy('write_file')
        def broken(observation):
            raise RuntimeError('observer failure')
        self.execution.observer = broken
        result = self.body(self.call('write_file', {'path': 'new.txt', 'content': 'once'}))
        self.assertTrue(result['ok'])
        self.assertTrue(result['observation_incomplete'])
        self.assertEqual(len(entered), 1)
        self.assertEqual((self.root / 'new.txt').read_text(), 'once')

    def test_complete_overwrite_rejects_unread_or_partially_read_file(self):
        self.engine.add_rule('allow', 'Write(*)')
        for read_args in (None, {'path': 'sample.txt', 'max_chars': 3}):
            with self.subTest(read=read_args):
                if read_args:
                    self.call('read_file', read_args)
                result = self.body(self.call('write_file', {'path': 'sample.txt', 'content': 'replacement'}))
                self.assertEqual(result['error_code'], 'read_required')
                self.assertFalse(result['started'])
                self.assertEqual((self.root / 'sample.txt').read_text(), 'before\nsecond\n')

    def test_consumed_pages_complete_same_version_before_overwrite(self):
        self.engine.add_rule('allow', 'Write(*)')
        page1 = self.body(self.call('read_file', {'path': 'sample.txt', 'max_chars': 5}))
        self.assertEqual((page1['content'], page1['content_offset']), ('befor', 0))
        self.assertFalse(page1['read_complete'])
        page2 = self.body(self.call('read_file', {'path': 'sample.txt', 'offset': page1['next_offset'],
            'char_offset': page1['next_char_offset']}))
        self.assertEqual((page2['content'], page2['content_offset']), ('e\nsecond\n', 5))
        self.assertTrue(page2['read_complete'])
        self.assertEqual(page1['version'], page2['version'])
        result = self.body(self.call('write_file', {'path': 'sample.txt', 'content': 'replacement'}))
        self.assertTrue(result['ok'])
        self.assertEqual((self.root / 'sample.txt').read_text(), 'replacement')

    def test_page_gaps_and_new_version_cannot_fake_complete_reading(self):
        self.engine.add_rule('allow', 'Write(*)')
        self.call('read_file', {'path': 'sample.txt', 'max_chars': 3})
        self.call('read_file', {'path': 'sample.txt', 'offset': 2})
        result = self.body(self.call('write_file', {'path': 'sample.txt', 'content': 'bad'}))
        self.assertEqual(result['error_code'], 'read_required')
        (self.root / 'sample.txt').write_text('changed\nsecond\n')
        self.call('read_file', {'path': 'sample.txt', 'offset': 2})
        result = self.body(self.call('write_file', {'path': 'sample.txt', 'content': 'bad'}))
        self.assertEqual(result['error_code'], 'read_required')
        self.assertEqual((self.root / 'sample.txt').read_text(), 'changed\nsecond\n')

    def test_content_offset_is_absolute_for_unicode_line_and_empty_selection(self):
        (self.root / 'sample.txt').write_text('甲乙\n丙丁戊\n尾')
        result = self.body(self.call('read_file', {'path': 'sample.txt', 'offset': 2, 'char_offset': 1, 'max_chars': 2}))
        self.assertEqual((result['content'], result['content_offset']), ('丁戊', 4))
        beyond = self.body(self.call('read_file', {'path': 'sample.txt', 'offset': 20}))
        self.assertIsNone(beyond['content_offset'])
        (self.root / 'empty.txt').touch()
        empty = self.body(self.call('read_file', {'path': 'empty.txt'}))
        self.assertEqual((empty['content'], empty['content_offset']), ('', 0))

    def test_json_budget_clipping_revokes_full_read(self):
        self.engine.add_rule('allow', 'Write(*)')
        (self.root / 'sample.txt').write_text('"' * 10000)
        page = self.body(self.call('read_file', {'path': 'sample.txt'}))
        self.assertTrue(page['result_truncated'])
        self.assertFalse(page['read_complete'])
        result = self.body(self.call('write_file', {'path': 'sample.txt', 'content': 'bad'}))
        self.assertEqual(result['error_code'], 'read_required')

    def test_approval_time_external_change_cannot_be_overwritten(self):
        self.call('read_file', {'path': 'sample.txt'})
        def approve(action, permission):
            (self.root / 'sample.txt').write_text('external user edit')
            self.session.read_file('sample.txt')
            return {'type': 'approve'}
        self.execution.approval_handler = approve
        result = self.body(self.call('write_file', {'path': 'sample.txt', 'content': 'bad'}))
        self.assertEqual(result['error_code'], 'file_conflict')
        self.assertEqual((self.root / 'sample.txt').read_text(), 'external user edit')

    def test_graph_interrupt_resume_preserves_original_approval_version(self):
        self.call('read_file', {'path': 'sample.txt'})
        self.execution.approval_handler = lambda *args: (_ for _ in ()).throw(GraphInterrupt(()))
        with self.assertRaises(GraphInterrupt):
            self.call('write_file', {'path': 'sample.txt', 'content': 'bad'}, call_id='pending')
        (self.root / 'sample.txt').write_text('external after interrupt')
        self.call('read_file', {'path': 'sample.txt'}, call_id='reread')
        self.execution.approval_handler = lambda *args: {'type': 'approve'}
        entered = self.spy('write_file')
        result = self.body(self.call('write_file', {'path': 'sample.txt', 'content': 'bad'}, call_id='pending'))
        self.assertEqual(result['error_code'], 'file_conflict')
        self.assertEqual(entered, [])
        self.assertEqual((self.root / 'sample.txt').read_text(), 'external after interrupt')

    def test_new_target_appearing_during_approval_is_preserved(self):
        def approve(*args):
            (self.root / 'new.txt').write_text('user created')
            return 'approve_once'
        self.execution.approval_handler = approve
        result = self.body(self.call('write_file', {'path': 'new.txt', 'content': 'bad'}))
        self.assertEqual(result['error_code'], 'file_conflict')
        self.assertEqual((self.root / 'new.txt').read_text(), 'user created')

    def test_read_only_child_never_requests_approval(self):
        self.engine.add_rule('ask', 'Read(sample.txt)')
        approvals = []
        self.execution.approval_handler = lambda *args: approvals.append(args) or 'approve_once'
        children = self.make_tools('subagent')
        entered = self.spy('read_file', children)
        result = self.body(self.call('read_file', {'path': 'sample.txt'}, tools=children))
        self.assertEqual(result['error_code'], 'approval_required')
        self.assertEqual(entered, [])
        self.assertEqual(approvals, [])

    def test_candidate_read_denies_apply_to_listing_glob_and_literal_search(self):
        (self.root / 'blocked.txt').write_text('secret needle')
        (self.root / 'allowed.txt').write_text('visible needle')
        self.engine.add_rule('deny', 'Read(blocked.txt)')
        for name, args in (('list_files', {}), ('glob', {'pattern': '*.txt'}),
                           ('search_text', {'query': 'needle'}),
                           ('grep', {'pattern': 'needle', 'pattern_mode': 'literal'})):
            with self.subTest(tool=name):
                result = self.body(self.call(name, args))
                exposed = result.get('files') or [item['path'] for item in result.get('matches', [])]
                self.assertIn('allowed.txt', exposed)
                self.assertNotIn('blocked.txt', exposed)
                self.assertEqual(result['coverage'], 'partial')
                self.assertNotIn('secret needle', json.dumps(result))

    def test_review_selected_file_listing_obeys_individual_permissions(self):
        (self.root / 'blocked.txt').write_text('secret')
        self.engine.add_rule('deny', 'Read(blocked.txt)')
        review = self.make_tools('review', target_path='.', review_paths=frozenset({'blocked.txt', 'sample.txt'}))
        result = self.body(self.call('list_files', {}, tools=review))
        self.assertEqual(result['files'], ['sample.txt'])
        self.assertEqual(result['coverage'], 'partial')

    def test_candidate_ask_is_skipped_without_requesting_broad_approval(self):
        (self.root / 'ask.txt').write_text('pending needle')
        self.engine.add_rule('ask', 'Read(ask.txt)')
        approvals = []
        self.execution.approval_handler = lambda *args: approvals.append(args) or 'approve_once'
        result = self.body(self.call('grep', {'pattern': 'needle', 'pattern_mode': 'literal'}))
        self.assertTrue(result['ok'])
        self.assertEqual(result['matches'], [])
        self.assertEqual(result['coverage'], 'partial')
        self.assertEqual(result['skipped_files'][0]['reason'], 'approval_required')
        self.assertEqual(approvals, [])

    def test_same_content_inode_replacement_during_approval_is_rejected(self):
        self.call('read_file', {'path': 'sample.txt'})
        def approve(*args):
            alternate = self.root / 'replacement.txt'
            alternate.write_text('before\nsecond\n')
            alternate.replace(self.root / 'sample.txt')
            return 'approve_once'
        self.execution.approval_handler = approve
        result = self.body(self.call('edit_file', {'path': 'sample.txt', 'old_string': 'before', 'new_string': 'bad'}))
        self.assertEqual(result['error_code'], 'file_conflict')
        self.assertEqual((self.root / 'sample.txt').read_text(), 'before\nsecond\n')

    def test_inherited_allow_rule_cannot_authorize_child_mutation(self):
        self.engine.add_rule('allow', 'Write(*)')
        entered = self.spy('write_file')
        spec = replace(self.tools['write_file'].spec, profiles=frozenset({'subagent'}))
        result = self.execution.invoke_sync(spec, {'path': 'new.txt', 'content': 'bad'},
            profile='subagent', file_session=self.session)
        self.assertEqual(result['error_code'], 'permission_denied')
        self.assertFalse(result['started'])
        self.assertEqual(entered, [])
        self.assertFalse((self.root / 'new.txt').exists())

    @unittest.skipUnless(shutil.which('rg'), 'ripgrep executable unavailable')
    def test_real_regex_search_filters_denied_files_and_bounds_context(self):
        (self.root / 'blocked.txt').write_text('SECRET needle42')
        (self.root / 'allowed.txt').write_text('x' * 5000 + '\nneedle42\n' + 'y' * 5000)
        self.engine.add_rule('deny', 'Read(blocked.txt)')
        result = self.body(self.call('grep', {'pattern': r'needle\d+', 'context': 1}))
        self.assertTrue(result['ok'])
        self.assertEqual(result['backend'], 'ripgrep')
        self.assertEqual([item['path'] for item in result['matches']], ['allowed.txt'])
        self.assertEqual(result['coverage'], 'partial')
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertLess(len(json.dumps(result)), 5000)

    def test_oversized_search_file_is_reported_as_partial_zero_match(self):
        (self.root / 'large.txt').write_text('needle' + 'x' * 200001)
        result = self.body(self.call('search_text', {'query': 'needle'}))
        self.assertEqual(result['matches'], [])
        self.assertEqual(result['coverage'], 'partial')
        self.assertFalse(result['coverage_complete'])
        self.assertIn('large.txt', [item['path'] for item in result['skipped_files']])

    def test_tool_result_paging_does_not_restore_full_read_eligibility(self):
        self.engine.add_rule('allow', 'Write(*)')
        (self.root / 'sample.txt').write_text('"' * 10000)
        clipped = self.body(self.call('read_file', {'path': 'sample.txt'}))
        self.assertTrue(clipped['result_truncated'])
        reference = clipped['reference']
        pieces = []
        offset = 0
        while True:
            message = self.call('read_tool_result', {'reference': reference, 'offset': offset})
            self.assertEqual(message.status, 'success')
            page = self.body(message)
            pieces.append(page['content'])
            if not page['truncated']:
                break
            offset = page['next_offset']
        self.assertEqual(json.loads(''.join(pieces))['content'], '"' * 10000)
        result = self.body(self.call('write_file', {'path': 'sample.txt', 'content': 'bad'}))
        self.assertEqual(result['error_code'], 'read_required')

    def test_archive_limit_is_explicit_when_original_is_too_large(self):
        archive = ResultArchive(self.root / 'limited')
        bounded = archive.bound({'ok': True, 'content': 'x' * 1000010})
        self.assertFalse(bounded['artifact_complete'])
        self.assertEqual(bounded['artifact_chars'], 1000000)
        last = archive.read(bounded['reference'], 999990, 30)
        self.assertFalse(last['truncated'])
        self.assertFalse(last['artifact_complete'])
        self.assertEqual(last['coverage'], 'partial')

    def test_invalid_schema_arguments_have_error_status_and_zero_handler(self):
        cases = [('read_file', {'path': 'sample.txt', 'offset': '1'}),
                 ('read_file', {'path': 'sample.txt', 'offset': True}),
                 ('read_file', {'path': 'sample.txt', 'offset': 0}),
                 ('read_file', {'path': 'sample.txt', 'limit': 1001}),
                 ('read_file', {'path': 'sample.txt', 'extra': 'bad'}),
                 ('grep', {'pattern': 'x', 'context': 6}),
                 ('grep', {'pattern': 'x', 'output_mode': 'everything'}),
                 ('run_command', {'command': 'true', 'timeout_seconds': 31}),
                 ('write_file', {'path': 'new.txt', 'content': 'x' * 120001})]
        for name, args in cases:
            with self.subTest(tool=name, arguments=list(args)):
                entered = self.spy(name)
                message = self.call(name, args)
                result = self.body(message)
                self.assertEqual(message.status, 'error')
                self.assertEqual(result['error_code'], 'invalid_parameters')
                self.assertFalse(result['started'])
                self.assertEqual(entered, [])

    def test_registered_schema_enforces_same_bounds_as_adapter(self):
        specs = {spec.name: spec for spec in build_tool_specs(session=self.session)}
        for name in ('read_file', 'grep', 'write_file'):
            self.assertEqual(self.tools[name].args_schema.model_json_schema(), specs[name].input_schema)
        self.assertEqual(specs['grep'].input_schema['properties']['pattern_mode']['enum'], ['regex', 'literal'])

    def test_backend_unavailable_does_not_silently_change_regex_semantics(self):
        with patch('nailong.tools.files.shutil.which', return_value=None):
            message = self.call('grep', {'pattern': 'before.*second'})
        self.assertEqual(message.status, 'error')
        self.assertEqual(self.body(message)['error_code'], 'backend_unavailable')
        literal = self.body(self.call('grep', {'pattern': 'before', 'pattern_mode': 'literal'}))
        self.assertTrue(literal['ok'])
        self.assertEqual(literal['matches'][0]['path'], 'sample.txt')

    def test_real_sync_command_failure_has_error_message_status(self):
        self.execution.approval_handler = lambda *args: 'approve_once'
        message = self.call('run_command', {'command': "printf 'diagnostic'; exit 7"})
        result = self.body(message)
        self.assertEqual(message.status, 'error')
        self.assertEqual(result['exit_code'], 7)
        self.assertIn('diagnostic', result['output'])

    def test_sync_command_keeps_final_summary_after_large_unicode_output(self):
        self.execution.approval_handler = lambda *args: 'approve_once'
        source = "print('BEGIN'); print('甲'*16000); print('FINAL_FAILURE_SUMMARY')"
        command = shlex.quote(sys.executable) + ' -c ' + shlex.quote(source)
        result = self.body(self.call('run_command', {'command': command}))
        self.assertTrue(result['ok'])
        self.assertTrue(result['output_truncated'])
        self.assertIn('BEGIN', result['output'])
        self.assertIn('FINAL_FAILURE_SUMMARY', result['output'])
        self.assertNotIn('\ufffd', result['output'])
        self.assertLessEqual(len(result['output']), 12000)

    def test_result_archive_is_bounded_redacted_and_capability_scoped(self):
        archive = ResultArchive(self.root / 'paged', api_key='secret-sentinel')
        original = {'ok': True, 'output': 'BEGIN secret-sentinel ' + 'x' * 20000 + ' END'}
        bounded = archive.bound(original)
        self.assertLessEqual(len(json.dumps(bounded, ensure_ascii=False)), 16000)
        self.assertNotIn('secret-sentinel', json.dumps(bounded, ensure_ascii=False))
        self.assertTrue(bounded['output_truncated'])
        self.assertIn('BEGIN', bounded['output'])
        self.assertIn('END', bounded['output'])
        reference = bounded['reference']
        parts = []
        offset = 0
        while True:
            page = archive.read(reference, offset, 6000)
            self.assertTrue(page['ok'])
            self.assertLessEqual(len(page['content']), 6000)
            parts.append(page['content'])
            if not page['truncated']:
                break
            self.assertEqual(page['coverage'], 'partial')
            offset = page['next_offset']
        saved = json.loads(''.join(parts))
        self.assertEqual(saved['output'], 'BEGIN [密钥已隐藏] ' + 'x' * 20000 + ' END')
        self.assertFalse(archive.read(str(self.root / 'sample.txt'))['ok'])
        self.assertFalse(ResultArchive().read(reference)['ok'])
        self.assertFalse(archive.read(reference, -1)['ok'])
        self.assertFalse(archive.read(reference, 0, 6001)['ok'])
        self.assertEqual(os.stat(self.root / 'paged' / (reference + '.json')).st_mode & 0o777, 0o600)

    def test_dynamic_read_metadata_is_honored_in_plan_mode(self):
        class Memory:
            def read_section(self, scope, section, **kwargs):
                return {'content': 'legacy memory'}
        plan = self.make_tools('plan', memory_store=Memory())
        result = self.body(self.call('read_memory', {'scope': 'project'}, tools=plan))
        self.assertTrue(result['ok'])
        self.assertEqual(result['content'], 'legacy memory')


class ManagedAsyncToolsTests(Workspace, unittest.IsolatedAsyncioTestCase):
    async def test_async_observer_receives_hashes_before_tool_return(self):
        received = []
        async def observe(observation):
            await asyncio.sleep(0)
            received.append(observation)
        self.execution.observer = observe
        self.engine.add_rule('allow', 'Write(*)')
        message = await self.acall('write_file', {'path': 'new.txt', 'content': 'private-written-content'})
        self.assertTrue(self.body(message)['ok'])
        self.assertEqual(len(received), 1)
        self.assertRegex(received[0]['arguments_digest'], r'^[0-9a-f]{64}$')
        self.assertRegex(received[0]['result_digest'], r'^[0-9a-f]{64}$')
        self.assertTrue(received[0]['changed'])
        self.assertNotIn('private-written-content', json.dumps(received))

    async def test_async_deny_is_zero_handler_and_error_toolmessage(self):
        self.engine.add_rule('deny', 'Read(sample.txt)')
        entered = self.spy('read_file')
        message = await self.acall('read_file', {'path': 'sample.txt'})
        self.assertEqual(message.status, 'error')
        self.assertEqual(self.body(message)['error_code'], 'permission_denied')
        self.assertEqual(entered, [])
        self.assertFalse(self.observations[-1]['started'])

    async def test_async_ask_awaits_approval_and_passes_trusted_profile(self):
        actions = []
        async def approve(action, permission):
            await asyncio.sleep(0)
            actions.append(action)
            return {'type': 'approve'}
        self.execution.approval_handler = approve
        result = self.body(await self.acall('write_file', {'path': 'new.txt', 'content': 'created'}))
        self.assertTrue(result['ok'])
        self.assertEqual((self.root / 'new.txt').read_text(), 'created')
        self.assertEqual(actions[0]['profile'], 'chat')

    async def test_async_child_ask_does_not_invoke_approval_callback(self):
        self.engine.add_rule('ask', 'Read(sample.txt)')
        approvals = []
        async def approve(*args):
            approvals.append(args)
            return 'approve_once'
        self.execution.approval_handler = approve
        children = self.make_tools('subagent')
        entered = self.spy('read_file', children)
        result = self.body(await self.acall('read_file', {'path': 'sample.txt'}, tools=children))
        self.assertEqual(result['error_code'], 'approval_required')
        self.assertEqual(approvals, [])
        self.assertEqual(entered, [])

    async def test_async_approval_keeps_version_bound_while_waiting(self):
        await self.acall('read_file', {'path': 'sample.txt'})
        pending = asyncio.Event()
        release = asyncio.Event()
        async def approve(*args):
            pending.set()
            await release.wait()
            return 'approve_once'
        self.execution.approval_handler = approve
        operation = asyncio.create_task(self.acall('write_file', {'path': 'sample.txt', 'content': 'bad'}))
        await asyncio.wait_for(pending.wait(), 2)
        (self.root / 'sample.txt').write_text('changed during async approval')
        await self.acall('read_file', {'path': 'sample.txt'}, call_id='new-read')
        release.set()
        result = self.body(await asyncio.wait_for(operation, 2))
        self.assertEqual(result['error_code'], 'file_conflict')
        self.assertEqual((self.root / 'sample.txt').read_text(), 'changed during async approval')

    async def test_sync_and_async_mutations_share_one_project_gate(self):
        other = FileSession(self.root)
        specs = {spec.name: spec for spec in build_tool_specs(session=self.session)}
        entered = asyncio.Event()
        release = asyncio.Event()
        async def process(command, cwd, *, timeout):
            (self.root / 'command.txt').write_text('command entered')
            entered.set()
            await release.wait()
            return {'ok': True, 'exit_code': 0, 'output': 'done', 'timed_out': False, 'output_truncated': False}
        with patch('nailong.core.processes.execute_process', side_effect=process):
            command = asyncio.create_task(specs['run_command'].async_handler('true'))
            await asyncio.wait_for(entered.wait(), 2)
            write = asyncio.create_task(asyncio.to_thread(other.write_file, 'new.txt', 'file mutation'))
            await asyncio.sleep(.03)
            self.assertFalse(write.done())
            self.assertFalse((self.root / 'new.txt').exists())
            release.set()
            self.assertTrue((await asyncio.wait_for(command, 2))['ok'])
            self.assertTrue((await asyncio.wait_for(write, 2))['ok'])
        self.assertEqual((self.root / 'new.txt').read_text(), 'file mutation')

    async def test_cancelled_async_waiter_never_leaks_project_lock(self):
        coordinator = self.session.mutation_lock
        async with coordinator.async_scope():
            async def wait_for_gate():
                async with coordinator.async_scope():
                    (self.root / 'bad.txt').write_text('should not happen')
            waiter = asyncio.create_task(wait_for_gate())
            await asyncio.sleep(.02)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
        async with asyncio.timeout(2):
            async with coordinator.async_scope():
                pass
        self.assertFalse((self.root / 'bad.txt').exists())

    async def test_cancelled_sync_worker_is_joined_before_reporting_interruption(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        def handler(path, **kwargs):
            started.set()
            if not release.wait(2):
                raise RuntimeError('test release timed out')
            (self.root / 'worker-finished.txt').write_text('completed once')
            finished.set()
            return {'ok': True}
        self.tools['read_file'].spec = replace(self.tools['read_file'].spec, handler=handler)
        operation = asyncio.create_task(self.acall('read_file', {'path': 'sample.txt'}))
        self.assertTrue(await asyncio.to_thread(started.wait, 2))
        operation.cancel()
        await asyncio.sleep(.02)
        operation.cancel()
        await asyncio.sleep(.02)
        self.assertFalse(operation.done())
        self.assertFalse(finished.is_set())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(operation, 2)
        self.assertTrue(finished.is_set())
        self.assertEqual((self.root / 'worker-finished.txt').read_text(), 'completed once')
        self.assertEqual(len(self.observations), 1)
        self.assertEqual(self.observations[0]['status'], 'interrupted')
        self.assertEqual(self.observations[0]['thread_id'], 'owner')
        self.assertTrue(self.observations[0]['started'])
        self.assertIsNone(active_file_version.get())
        self.assertIsNone(current_tool_execution.get())

    async def test_cancellation_during_approval_is_unstarted(self):
        entered = asyncio.Event()
        async def approve(*args):
            entered.set()
            await asyncio.Event().wait()
        self.execution.approval_handler = approve
        operation = asyncio.create_task(self.acall('write_file', {'path': 'new.txt', 'content': 'bad'}))
        await asyncio.wait_for(entered.wait(), 2)
        operation.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await operation
        self.assertFalse((self.root / 'new.txt').exists())
        self.assertFalse(self.observations[-1]['started'])
        self.assertEqual(self.observations[-1]['status'], 'interrupted')

    async def test_real_async_command_failure_has_error_toolmessage(self):
        self.execution.approval_handler = lambda *args: 'approve_once'
        message = await self.acall('run_command', {'command': "printf 'async diagnostic'; exit 9"})
        result = self.body(message)
        self.assertEqual(message.status, 'error')
        self.assertEqual(result['exit_code'], 9)
        self.assertIn('async diagnostic', result['output'])

    async def test_approved_async_command_cancellation_stops_process_and_late_effect(self):
        self.execution.approval_handler = lambda *args: 'approve_once'
        source = "import os,time; open('pid','w').write(str(os.getpid())); time.sleep(.4); open('late','w').write('bad')"
        command = shlex.quote(sys.executable) + ' -c ' + shlex.quote(source)
        operation = asyncio.create_task(self.acall('run_command', {'command': command}))
        try:
            async with asyncio.timeout(2):
                while not (self.root / 'pid').exists():
                    if operation.done():
                        self.fail('command ended before PID marker: ' + str(await operation))
                    await asyncio.sleep(.01)
            pid = int((self.root / 'pid').read_text())
            operation.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(operation, 2)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            await asyncio.sleep(.45)
            self.assertFalse((self.root / 'late').exists())
            self.assertEqual(self.observations[-1]['status'], 'interrupted')
            self.assertTrue(self.observations[-1]['started'])
        finally:
            if not operation.done():
                operation.cancel()
                try:
                    await operation
                except asyncio.CancelledError:
                    pass

    async def test_async_schema_validation_rejects_unknown_fields(self):
        entered = self.spy('read_file')
        message = await self.acall('read_file', {'path': 'sample.txt', 'unexpected': 'bad'})
        self.assertEqual(message.status, 'error')
        self.assertEqual(self.body(message)['error_code'], 'invalid_parameters')
        self.assertEqual(entered, [])


if __name__ == '__main__':
    unittest.main()
