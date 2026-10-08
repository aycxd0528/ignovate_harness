"""Current evidence for bounded memory provenance; no provider calls."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from langchain_core.messages import ToolMessage

from nailong.core.memory import MemorySnapshot, MemoryStore, load_project_memory
from nailong.core.memory_context import MemoryReadContext, estimate_memory_tokens
from nailong.core.memory_knowledge import (
    MAX_SOURCE_BYTES, MAX_SOURCE_CHECKS, SourceValidator, declaration, parse_frontmatter,
)
from nailong.core.memory_selection import select_core_memory
from nailong.core.permissions import Decision, PermissionEngine


class ManagedMemoryTests(unittest.TestCase):
    maxDiff = 1500

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='nailong-managed-memory-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'project'
        self.root.mkdir()
        self.user_file = self.base / 'user/.nailong/context.md'
        self.store = MemoryStore(self.root, user_file=self.user_file, task_scope=['.'])

    def source(self, relative='src/build.py', body=b'original dependency'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return {'kind': 'file', 'path': relative, 'sha256': hashlib.sha256(body).hexdigest()}

    def document(self, *, scope='project', filename='context.md', sources=None,
                 state='candidate', applicability=None, body='Remember the build convention.', **extra):
        if sources is None:
            sources = [self.source()]
        knowledge = {'type': 'project_fact', 'state': state, 'origin': 'runtime', 'sources': sources}
        if state == 'confirmed':
            knowledge['confirmed_by'] = 'user'
        if applicability is not None:
            knowledge['applicability'] = applicability
        knowledge.update(extra)
        text = '---\n' + yaml.safe_dump({'description': 'Build knowledge', 'knowledge': knowledge},
                                       allow_unicode=True, sort_keys=False) + '---\n' + body
        target = self.store.document_path(scope + '/' + filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8', newline='\n')
        return target

    def snapshot(self, budget=4000):
        core = select_core_memory(self.store)
        return MemorySnapshot(core, store=self.store, reports=core.reports, budget_tokens=budget)

    def settings(self, permissions):
        path = self.root / '.nailong/settings.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({'permissions': permissions}), encoding='utf-8', newline='\n')

    def test_source_change_and_removal_invalidate_both_catalog_and_read(self):
        source = self.source()
        self.document(sources=[source], state='confirmed')
        self.assertEqual(self.store.read_document('project/context.md')['validity'], 'observed')
        (self.root / source['path']).write_bytes(b'changed dependency')
        for result in (self.store.catalog()['documents'][0], self.store.read_document('project/context.md')):
            self.assertEqual(result['validity'], 'stale')
            self.assertEqual(result['knowledge_state'], 'confirmed')
            self.assertFalse(result['fact_verified'])
            self.assertEqual(result['sources'][0]['reason'], 'source_digest_changed')
        (self.root / source['path']).unlink()
        result = self.store.read_document('project/context.md')
        self.assertEqual(result['validity'], 'stale')
        self.assertEqual(result['sources'][0]['reason'], 'source_missing')

    def test_matching_sources_do_not_promote_candidate_or_verify_fact(self):
        target = self.document()
        original = target.read_bytes()
        snapshot = self.snapshot()
        context = MemoryReadContext(snapshot)
        for result in (context.read('project/context.md'), context.list()['documents'][0], snapshot.reports['project']):
            self.assertEqual(result['knowledge_state'], 'candidate')
            self.assertEqual(result['validity'], 'observed')
            self.assertFalse(result['fact_verified'])
        context.refresh_for_request()
        self.assertEqual(target.read_bytes(), original, '观察不得写入或自动确认知识')

    def test_legacy_documents_remain_readable_without_dependency_io(self):
        target = self.store.path('project'); target.parent.mkdir()
        target.write_text('Legacy project memory', encoding='utf-8', newline='\n')
        with patch.object(self.store, 'authorize_source', side_effect=AssertionError('unexpected source check')):
            result = self.store.read_document('project/context.md')
            snapshot = self.snapshot()
        self.assertTrue(result['ok'])
        self.assertEqual(result['content'], 'Legacy project memory')
        self.assertEqual(result['knowledge_state'], 'legacy')
        self.assertEqual(result['validity'], 'unverified')
        self.assertIn('Legacy project memory', snapshot.rendered_context)

    def test_persisted_deny_and_ask_prevent_dependency_bytes(self):
        self.document()
        for effect in ('deny', 'ask'):
            with self.subTest(effect=effect):
                self.settings({effect: ['Read(./src/**)']})
                with patch.object(self.store, '_read_bytes_path', wraps=self.store._read_bytes_path) as reads:
                    result = self.store.read_document('project/context.md')
                source_reads = [call for call in reads.call_args_list if call.args[0] == self.root / 'src/build.py']
                self.assertEqual(source_reads, [])
                self.assertEqual(result['validity'], 'unverified')
                self.assertEqual(result['sources'][0]['reason'], 'source_permission_not_allowed')

    def test_session_grant_and_modes_use_live_authorization(self):
        self.document()
        engine = PermissionEngine(self.root, rules={'ask': ['Read(./src/**)']})
        for mode in ('default', 'plan', 'acceptEdits'):
            with self.subTest(mode=mode):
                self.store.source_authorizer = lambda relative: engine.decide_action(
                    'read_file', {'path': relative}, mode=mode).decision == Decision.ALLOW
                result = self.store.read_document('project/context.md')
                self.assertEqual(result['validity'], 'unverified')
        engine.grant_session('Read(./src/**)')
        self.assertEqual(self.store.read_document('project/context.md')['validity'], 'observed')
        engine.add_rule('deny', 'Read(./src/**)')
        self.assertEqual(self.store.read_document('project/context.md')['validity'], 'unverified')

    def test_truthy_or_broken_callback_cannot_authorize_source(self):
        self.document()
        for answer in ('allow', 'ask', 1, None):
            self.store.source_authorizer = lambda relative, value=answer: value
            self.assertEqual(self.store.read_document('project/context.md')['validity'], 'unverified')
        def broken(relative):
            raise RuntimeError('fake-sensitive-callback-detail')
        self.store.source_authorizer = broken
        result = self.store.read_document('project/context.md')
        self.assertEqual(result['validity'], 'unverified')
        self.assertNotIn('fake-sensitive-callback-detail', json.dumps(result))

    def test_unknown_or_nonmatching_task_scope_does_not_observe_current_fact(self):
        self.document(applicability={'paths': ['src']})
        self.store.task_scope = None
        unknown = self.store.read_document('project/context.md')
        self.assertIsNone(unknown['applicable'])
        self.assertEqual(unknown['validity'], 'unverified')
        self.store.task_scope = ('docs',)
        with patch.object(self.store, 'authorize_source') as authorize:
            irrelevant = self.store.read_document('project/context.md')
        authorize.assert_not_called()
        self.assertFalse(irrelevant['applicable'])
        self.store.task_scope = ('src/build.py',)
        self.assertEqual(self.store.read_document('project/context.md')['validity'], 'observed')

    def test_project_identity_mismatch_prevents_dependency_reads(self):
        self.document(applicability={'project_id': 'f' * 64})
        with patch.object(self.store, 'authorize_source') as authorize:
            result = self.store.read_document('project/context.md')
        authorize.assert_not_called()
        self.assertFalse(result['applicable'])
        self.assertIn('different_project', result['knowledge_issues'])

    def test_user_file_sources_require_matching_project_identity(self):
        self.document(scope='user')
        invalid = self.store.read_document('user/context.md')
        self.assertFalse(invalid['ok'])
        self.assertIn('user_file_sources_require_project_id', invalid['knowledge_issues'])
        self.document(scope='user', applicability={'scope': 'user', 'project_id': self.store.project_id})
        self.assertEqual(self.store.read_document('user/context.md')['validity'], 'observed')
        other_root = self.base / 'other'; other_root.mkdir()
        (other_root / 'src').mkdir(); (other_root / 'src/build.py').write_bytes(b'original dependency')
        other = MemoryStore(other_root, user_file=self.user_file, task_scope=['.'])
        with patch.object(other, 'authorize_source') as authorize:
            result = other.read_document('user/context.md')
        authorize.assert_not_called()
        self.assertFalse(result['applicable'], '同名同内容不能将用户知识跨项目验证')

    def test_duplicate_yaml_keys_fail_before_dependency_authorization(self):
        target = self.document()
        text = target.read_text().replace('  state: candidate', '  state: candidate\n  state: confirmed')
        target.write_text(text, newline='\n')
        with patch.object(self.store, 'authorize_source') as authorize:
            result = self.store.read_document('project/context.md')
        authorize.assert_not_called()
        self.assertFalse(result['ok'])
        self.assertIn('duplicate_frontmatter_key', result['knowledge_issues'])
        snapshot = self.snapshot()
        self.assertEqual(snapshot.reports['project']['status'], 'error')
        self.assertNotIn('Remember the build convention.', snapshot.rendered_context)

    def test_yaml_merge_cannot_silently_override_knowledge_state(self):
        target = self.document()
        text = target.read_text().replace('knowledge:\n', 'knowledge:\n  <<: &base {state: confirmed}\n')
        target.write_text(text, newline='\n')
        with patch.object(self.store, 'authorize_source') as authorize:
            result = self.store.read_document('project/context.md')
        authorize.assert_not_called()
        self.assertFalse(result['ok'])
        self.assertIn('duplicate_frontmatter_key', result['knowledge_issues'])

    def test_frontmatter_limits_and_yaml_key_shapes_fail_before_source_checks(self):
        cases = ('---\ndescription: ' + 'x' * 8192 + '\n---\nnever inject',
                 '---\nknowledge: {}\nnever inject',
                 '---\n? [bad, key]\n: value\n---\nnever inject')
        target = self.document()
        for text in cases:
            with self.subTest(prefix=text[:50]):
                target.write_text(text, newline='\n')
                with patch.object(self.store, 'authorize_source') as authorize:
                    result = self.store.read_document('project/context.md')
                authorize.assert_not_called()
                self.assertFalse(result['ok'])
                self.assertEqual(result['metadata_status'], 'invalid')
                self.assertNotIn('never inject', json.dumps(result))

    def test_malformed_persisted_permissions_fail_closed_without_source_bytes(self):
        self.document()
        for rules in ({'deny': 'Read(./src/**)'}, {'deny': [5]},
                      {'deny': ['Read(./src/**']}, {'unexpected': []}):
            with self.subTest(rules=rules):
                self.settings(rules)
                with patch.object(self.store, '_read_bytes_path', wraps=self.store._read_bytes_path) as reads:
                    result = self.store.read_document('project/context.md')
                source_reads = [call for call in reads.call_args_list if call.args[0] == self.root / 'src/build.py']
                self.assertEqual(source_reads, [])
                self.assertEqual(result['validity'], 'unverified')
                self.assertNotIn('current_sha256', result['sources'][0])

    def test_invalid_shapes_and_excessive_metadata_do_not_read_dependencies(self):
        source = self.source()
        cases = ({'sources': [source] * 9}, {'type': ['project_fact']}, {'state': 'confirmed', 'confirmed_by': None},
                 {'sources': [{'kind': 'reference', 'ref': 'user:' + 'x' * 200}]},
                 {'sources': [dict(source, path='x' * 513)]}, {'applicability': {'paths': ['src'] * 17}})
        for overrides in cases:
            with self.subTest(overrides=str(overrides)[:90]):
                self.document(**overrides)
                with patch.object(self.store, 'authorize_source') as authorize:
                    result = self.store.read_document('project/context.md')
                authorize.assert_not_called()
                self.assertFalse(result['ok'])

    def test_reference_only_is_unverified_without_lookup(self):
        self.document(sources=[{'kind': 'reference', 'ref': 'session:thread-id#event:event-id'}])
        with patch.object(self.store, 'authorize_source') as authorize:
            result = self.store.read_document('project/context.md')
        authorize.assert_not_called()
        self.assertEqual(result['validity'], 'unverified')
        self.assertEqual(result['sources'][0]['reason'], 'reference_not_resolved')

    def test_protected_and_traversal_sources_are_rejected_before_open(self):
        source = self.source()
        for relative in ('.env', '.git/config', '.venv/config', '.ssh/id_rsa', '../outside',
                         '/tmp/outside', 'secrets.json', 'src/private.pem', 'src/id_ed25519'):
            with self.subTest(relative=relative):
                self.document(sources=[dict(source, path=relative)])
                self.store.source_authorizer = lambda path: True
                with patch.object(self.store, 'authorize_source') as authorize:
                    result = self.store.read_document('project/context.md')
                authorize.assert_not_called()
                self.assertFalse(result['ok'])

    def test_linked_dependency_never_returns_outside_digest(self):
        source = self.source()
        target = self.root / source['path']; target.unlink()
        outside = self.base / 'outside'; outside.write_bytes(b'outside-sensitive-sentinel')
        target.symlink_to(outside)
        self.document(sources=[source])
        self.store.source_authorizer = lambda relative: True
        result = self.store.read_document('project/context.md')
        self.assertEqual(result['validity'], 'unverified')
        self.assertNotIn(hashlib.sha256(outside.read_bytes()).hexdigest(), json.dumps(result))

    def test_source_size_and_operation_limits_are_explicit(self):
        source = self.source(body=b'x' * (MAX_SOURCE_BYTES + 1))
        self.document(sources=[source])
        result = self.store.read_document('project/context.md')
        self.assertEqual(result['validity'], 'unverified')
        self.assertNotIn('current_sha256', result['sources'][0])
        validator = SourceValidator(self.store)
        validator.remaining_bytes = 0
        metadata = declaration(parse_frontmatter(self.store.read('project')), 'project')
        with patch.object(self.store, 'authorize_source') as authorize:
            result = validator.evaluate(metadata)
        authorize.assert_not_called()
        self.assertEqual(result['sources'][0]['reason'], 'source_budget_exhausted')

    def test_catalog_small_dependencies_within_actual_io_budget_remain_observed(self):
        for index in range(10):
            source = self.source(f'src/file{index}.py', f'small {index}'.encode())
            self.document(filename=f'topic{index}.md', sources=[source])
        result = self.store.catalog()
        self.assertEqual(len(result['documents']), 10)
        self.assertEqual([row['validity'] for row in result['documents']], ['observed'] * 10,
                         '小文件只消耗实际读取额度，不能每次按整个1 MiB收费')

    def test_total_byte_limit_allows_exact_fit_and_cache_does_not_charge_twice(self):
        sources = [self.source('src/a.py', b'1234'), self.source('src/b.py', b'xyz'),
                   self.source('src/empty.py', b'')]
        self.document(sources=sources)
        metadata = declaration(parse_frontmatter(self.store.read('project')), 'project')
        validator = SourceValidator(self.store)
        validator.remaining_bytes = 10  # 4 + 3 + 0 bytes, plus three one-byte probes.
        with patch.object(self.store, '_read_bytes_path', wraps=self.store._read_bytes_path) as reads:
            result = validator.evaluate(metadata)
            repeated = validator.evaluate(metadata)
        self.assertEqual(result['validity'], 'observed')
        self.assertEqual(repeated['validity'], 'observed')
        self.assertEqual(validator.remaining_bytes, 0)
        self.assertEqual(reads.call_count, 3)
        self.assertEqual([call.args[2] for call in reads.call_args_list], [9, 4, 0])
        additional = self.source('src/extra.py', b'extra')
        with patch.object(self.store, 'authorize_source') as authorize:
            exhausted = validator._source(additional)
        authorize.assert_not_called()
        self.assertEqual(exhausted['reason'], 'source_budget_exhausted')

    def test_failed_post_read_check_keeps_reserved_io_charged(self):
        source = self.source(body=b'ab')
        additional = self.source('src/next.py', b'next')
        validator = SourceValidator(self.store)
        validator.remaining_bytes = 8
        original_reader = self.store._read_bytes_path
        def fail_after_read(path, revalidate, limit):
            self.assertEqual(original_reader(path, revalidate, limit), b'ab')
            raise ValueError('simulated post-read race')
        with patch.object(self.store, '_read_bytes_path', side_effect=fail_after_read):
            failed = validator._source(source)
        self.assertEqual(failed['status'], 'unverified')
        self.assertEqual(validator.remaining_bytes, 0)
        with patch.object(self.store, 'authorize_source') as authorize:
            result = validator._source(additional)
        authorize.assert_not_called()
        self.assertEqual(result['reason'], 'source_budget_exhausted')

    def test_catalog_has_bounded_unique_source_checks(self):
        for index in range(MAX_SOURCE_CHECKS + 1):
            source = self.source(f'src/file{index:03}.py', b'small')
            self.document(filename=f'topic{index:03}.md', sources=[source])
        with patch.object(self.store, 'authorize_source', wraps=self.store.authorize_source) as authorize:
            catalog = self.store.catalog()
        self.assertEqual(authorize.call_count, MAX_SOURCE_CHECKS)
        rows = catalog['documents']
        self.assertEqual(sum(row['validity'] == 'observed' for row in rows), MAX_SOURCE_CHECKS)
        self.assertEqual(rows[-1]['sources'][0]['reason'], 'source_validation_limit')

    def test_source_replacement_after_read_is_unverified(self):
        source = self.source()
        self.document(sources=[source])
        original_path = self.store.source_path
        calls = 0
        def swap_after_read(relative):
            nonlocal calls
            calls += 1
            if calls == 4:  # Initial path, authorization, before read, after read.
                target = self.root / relative
                replacement = target.with_suffix('.replacement')
                replacement.write_bytes(b'replacement sentinel')
                replacement.replace(target)
            return original_path(relative)
        with patch.object(self.store, 'source_path', side_effect=swap_after_read):
            result = self.store.read_document('project/context.md')
        self.assertEqual(calls, 4)
        self.assertEqual(result['validity'], 'unverified')
        self.assertNotIn('current_sha256', result['sources'][0])

    def test_source_limit_boundary_is_readable_and_never_exposes_dependency_bytes(self):
        prefix = b'fake-sensitive-source' * 16
        source = self.source(body=prefix + b'x' * (MAX_SOURCE_BYTES - len(prefix)))
        self.assertEqual((self.root / source['path']).stat().st_size, MAX_SOURCE_BYTES)
        self.document(sources=[source])
        result = self.store.read_document('project/context.md')
        self.assertEqual(result['validity'], 'observed')
        self.assertNotIn('fake-sensitive-source', json.dumps(result))

    def test_loader_preserves_callback_scope_and_rejects_unbounded_task_scope(self):
        self.document(applicability={'paths': ['src']})
        def authorize(relative):
            return relative == 'src/build.py'
        snapshot = load_project_memory(self.root, user_file=self.user_file,
                                       source_authorizer=authorize, task_scope=['src'])
        self.assertIs(snapshot.store.source_authorizer, authorize)
        self.assertEqual(snapshot.store.task_scope, ('src',))
        self.assertEqual(snapshot.reports['project']['validity'], 'observed')
        for scope in ('src', ['src'] * 129, ['../outside'], ['/tmp/outside']):
            with self.subTest(scope=str(scope)[:60]), self.assertRaises(ValueError):
                MemoryStore(self.root, user_file=self.user_file, task_scope=scope)

    def test_refresh_updates_core_catalog_and_historical_view_consistently(self):
        source = self.source()
        self.document(sources=[source])
        snapshot = self.snapshot(); context = MemoryReadContext(snapshot)
        original_result = self.store.read_document('project/context.md')
        original_message = ToolMessage(content=json.dumps(original_result), name='memory_read', tool_call_id='call')
        before = original_message.content
        (self.root / source['path']).write_bytes(b'new dependency')
        view = context.refresh_for_request()
        filtered = context.filter_messages([original_message])
        self.assertIn('validity=stale', view['context'])
        for row in (view['report']['scopes']['project'], context.list()['documents'][0], json.loads(filtered[0].content)):
            self.assertEqual(row['validity'], 'stale')
            self.assertFalse(row['fact_verified'])
        self.assertEqual(original_message.content, before)
        self.assertEqual(filtered[0].tool_call_id, 'call')

    def test_live_scope_changes_refresh_applicability_without_rebinding_or_extra_budget(self):
        target = self.document(applicability={'paths': ['src']}, body='scoped memory ' * 500)
        snapshot = self.snapshot(1024)
        current = {'scope': ['src']}
        context = MemoryReadContext(snapshot, task_scope_provider=lambda: current['scope'])
        original_versions = dict(context.versions)
        initial_context = context.initial_context
        original_document = target.read_bytes()
        original_result = self.store.read_document('project/context.md')
        original_message = ToolMessage(content=json.dumps(original_result), name='memory_read', tool_call_id='scope')
        for scope, applicable, validity in ((['src'], True, 'observed'), (['docs'], False, 'unverified'),
                                           (None, None, 'unverified')):
            with self.subTest(scope=scope):
                current['scope'] = scope
                view = context.refresh_for_request()
                listed = context.list()['documents'][0]
                read = context.read('project/context.md')
                history = json.loads(context.filter_messages([original_message])[0].content)
                for result in (view['report']['scopes']['project'], listed, read, history):
                    self.assertEqual(result['applicable'], applicable)
                    self.assertEqual(result['validity'], validity)
                    self.assertEqual(result['knowledge_state'], 'candidate')
                    self.assertFalse(result['fact_verified'])
                self.assertEqual(context.versions, original_versions)
                self.assertEqual(context.initial_context, initial_context)
                self.assertEqual(snapshot.budget_tokens, 1024)
                self.assertLessEqual(estimate_memory_tokens(view['context']) +
                                     estimate_memory_tokens(json.dumps(read, ensure_ascii=False)), 1024)
        self.assertEqual(target.read_bytes(), original_document)
        self.assertEqual(original_message.content, json.dumps(original_result))

    def test_invalid_live_scope_clears_old_scope_and_stops_refresh(self):
        self.document(applicability={'paths': ['src']})
        snapshot = self.snapshot()
        current = {'scope': ['src']}
        context = MemoryReadContext(snapshot, task_scope_provider=lambda: current['scope'])
        for scope in ('src', {}, ['src'] * 129, ['../outside'], ['/tmp/outside'], [True], ['.env']):
            with self.subTest(scope=str(scope)[:60]):
                self.store.task_scope = ('src',)
                current['scope'] = scope
                with patch.object(snapshot, 'refresh_metadata', wraps=snapshot.refresh_metadata) as refresh:
                    with self.assertRaisesRegex(ValueError, 'memory_task_scope_refresh_failed'):
                        context.refresh_for_request()
                refresh.assert_not_called()
                self.assertIsNone(self.store.task_scope)

    def test_live_scope_provider_failure_is_visible_without_keeping_old_scope(self):
        self.document(applicability={'paths': ['src']})
        def fail():
            raise RuntimeError('fake-sensitive-provider-detail')
        context = MemoryReadContext(self.snapshot(), task_scope_provider=fail)
        self.store.task_scope = ('src',)
        with self.assertRaisesRegex(ValueError, 'memory_task_scope_refresh_failed') as error:
            context.refresh_for_request()
        self.assertNotIn('fake-sensitive-provider-detail', str(error.exception))
        self.assertIsNone(self.store.task_scope)
        self.assertEqual(context.read('project/context.md')['validity'], 'unverified')

    def test_live_scope_constructor_requires_sync_callback_and_rejects_awaitables(self):
        self.document(applicability={'paths': ['src']})
        snapshot = self.snapshot()
        async def async_provider():
            return ['src']
        class AsyncProvider:
            async def __call__(self):
                return ['src']
        for provider in ('src', ['src'], 0, async_provider, AsyncProvider()):
            with self.subTest(provider=type(provider).__name__), self.assertRaises(ValueError):
                MemoryReadContext(snapshot, task_scope_provider=provider)
        with self.assertRaises(TypeError):
            MemoryReadContext(snapshot, lambda: ['src'])
        context = MemoryReadContext(snapshot, task_scope_provider=lambda: async_provider())
        with self.assertRaisesRegex(ValueError, 'memory_task_scope_refresh_failed'):
            context.refresh_for_request()
        self.assertIsNone(self.store.task_scope)

    def test_live_scope_is_copied_and_root_tuple_scope_is_supported(self):
        self.document(applicability={'paths': ['src']})
        current = {'scope': ['src']}
        context = MemoryReadContext(self.snapshot(), task_scope_provider=lambda: current['scope'])
        context.refresh_for_request()
        current['scope'].append('docs')
        self.assertEqual(self.store.task_scope, ('src',))
        current['scope'] = ('.',)
        view = context.refresh_for_request()
        self.assertEqual(self.store.task_scope, ('.',))
        self.assertTrue(view['report']['scopes']['project']['applicable'])

    def test_live_scopes_are_bound_to_independent_snapshot_stores(self):
        self.document(applicability={'paths': ['src']})
        first_snapshot = self.snapshot(1024)
        first = MemoryReadContext(first_snapshot, task_scope_provider=lambda: ['src'])
        other_store = MemoryStore(self.root, user_file=self.user_file, task_scope=['src'])
        core = select_core_memory(other_store)
        second_snapshot = MemorySnapshot(core, store=other_store, reports=core.reports, budget_tokens=1024)
        current = {'scope': ['docs']}
        second = MemoryReadContext(second_snapshot, task_scope_provider=lambda: current['scope'])
        self.assertIsNot(first_snapshot.store, second_snapshot.store)
        first.refresh_for_request()
        view = second.refresh_for_request()
        self.assertFalse(view['report']['scopes']['project']['applicable'])
        current['scope'] = None
        second.refresh_for_request()
        self.assertEqual(first_snapshot.store.task_scope, ('src',))
        self.assertIsNone(second_snapshot.store.task_scope)
        self.assertEqual(first.refresh_for_request()['report']['scopes']['project']['validity'], 'observed')
        self.assertEqual(first_snapshot.budget_tokens, second_snapshot.budget_tokens)

    def test_live_scope_refresh_does_not_rebind_changed_document_versions(self):
        target = self.document(applicability={'paths': ['src']}, body='old scoped body ' * 500)
        current = {'scope': ['src']}
        context = MemoryReadContext(self.snapshot(), task_scope_provider=lambda: current['scope'])
        page = context.read('project/context.md', limit=120)
        self.assertTrue(page['ok'])
        original_versions = dict(context.versions)
        target.write_text(target.read_text().replace('old scoped body', 'new scoped body'), newline='\n')
        current['scope'] = ['docs']
        view = context.refresh_for_request()
        self.assertEqual(context.versions, original_versions)
        self.assertEqual(view['report']['scopes']['project']['validity'], 'stale')
        self.assertNotIn('new scoped body', view['context'])
        self.assertEqual(context.read('project/context.md', offset=page['next_offset'])['status'], 'changed')

    def test_permission_revocation_updates_core_catalog_and_history_together(self):
        self.document()
        engine = PermissionEngine(self.root, rules={'ask': ['Read(./src/**)']})
        engine.grant_session('Read(./src/**)')
        self.store.source_authorizer = lambda relative: engine.decide_action(
            'read_file', {'path': relative}, mode='plan').decision == Decision.ALLOW
        context = MemoryReadContext(self.snapshot())
        original = self.store.read_document('project/context.md')
        message = ToolMessage(content=json.dumps(original), name='memory_read', tool_call_id='grant')
        self.assertEqual(original['validity'], 'observed')
        engine.add_rule('deny', 'Read(./src/**)')
        with patch.object(self.store, '_read_bytes_path', wraps=self.store._read_bytes_path) as reads:
            view = context.refresh_for_request()
            catalog = context.list()['documents'][0]
            history = json.loads(context.filter_messages([message])[0].content)
        source_reads = [call for call in reads.call_args_list if call.args[0] == self.root / 'src/build.py']
        self.assertEqual(source_reads, [])
        for result in (view['report']['scopes']['project'], catalog, history):
            self.assertEqual(result['validity'], 'unverified')
            self.assertEqual(result['knowledge_state'], 'candidate')
            self.assertFalse(result['fact_verified'])
        self.assertEqual(message.content, json.dumps(original))

    def test_refresh_does_not_rebind_memory_version_or_mix_pagination(self):
        target = self.document(body='old body ' * 500)
        context = MemoryReadContext(self.snapshot())
        first = context.read('project/context.md', 0, 120)
        self.assertTrue(first['ok'])
        old_version = first['version']
        target.write_text(target.read_text().replace('old body', 'new body'), newline='\n')
        view = context.refresh_for_request()
        self.assertEqual(view['report']['scopes']['project']['version'], old_version)
        self.assertEqual(view['report']['scopes']['project']['validity'], 'stale')
        self.assertNotIn('new body', view['context'])
        self.assertEqual(context.read('project/context.md', first['next_offset']).get('status'), 'changed')
        self.assertTrue(MemoryReadContext(self.snapshot()).read('project/context.md')['ok'])

    def test_historical_version_keeps_own_source_declaration(self):
        source = self.source()
        target = self.document(sources=[source])
        context = MemoryReadContext(self.snapshot())
        old = self.store.read_document('project/context.md')
        replacement = self.source('src/replacement.py', b'different reference')
        self.document(sources=[replacement])
        message = ToolMessage(content=json.dumps(old), name='memory_read', tool_call_id='old-call')
        payload = json.loads(context.filter_messages([message])[0].content)
        self.assertEqual(payload['validity'], 'stale')
        self.assertEqual(payload['sources'][0]['path'], source['path'])
        self.assertEqual(payload['sources'][0]['status'], 'unverified')
        self.assertNotIn(replacement['path'], json.dumps(payload))

    def test_low_budget_retains_epistemic_state_and_page_progress(self):
        self.document(body='中文正文' * 3000, state='confirmed')
        snapshot = self.snapshot(512); context = MemoryReadContext(snapshot)
        result = context.read('project/context.md')
        self.assertTrue(result['ok'])
        self.assertEqual(result['knowledge_state'], 'confirmed')
        self.assertEqual(result['validity'], 'observed')
        self.assertFalse(result['fact_verified'])
        self.assertGreater(result['next_offset'], 0)
        self.assertLessEqual(estimate_memory_tokens(snapshot.rendered_context) +
                             estimate_memory_tokens(json.dumps(result, ensure_ascii=False)), 512)


if __name__ == '__main__':
    unittest.main()
