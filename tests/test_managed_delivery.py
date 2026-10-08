import copy
import importlib
import json
import tempfile
import unittest
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage

from nailong.core.delivery import build_delivery_report, render_delivery_report


def snapshot(kind='review'):
    return {
        'schema_version': 1, 'task_id': 'task', 'thread_id': 'thread',
        'project_root': '/fixture/project', 'revision': 2,
        'objective': '检查当前文件', 'scope': ['a.py'], 'constraints': [],
        'latest_request': '检查当前文件', 'lifecycle': 'active', 'phase': 'deliver',
        'steps': [], 'acceptance': [{'id': 'review:scope', 'description': '审查流程覆盖',
            'kind': kind, 'required': True, 'status': 'pending', 'evidence_ids': []}],
        'changed_paths': [], 'pending_verification': [], 'blockers': [], 'evidence': [],
    }


def page(call_id, content, *, offset=0, total=6, version='file-v1', path='a.py', **extra):
    result = {'ok': True, 'path': path, 'version': version, 'content_offset': offset,
        'content': content, 'total_chars': total, 'truncated': offset + len(content) < total,
        'read_complete': False, 'coverage': 'partial', **extra}
    return ToolMessage(name='read_file', tool_call_id=call_id,
        content=json.dumps(result, ensure_ascii=False))


class ReviewCoverageTests(unittest.TestCase):
    def collector(self, paths=('a.py',), **kwargs):
        # A missing implementation is an explicit failing feature assertion.
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('nailong.core.review_evidence'),
            '缺少实际请求静态审查覆盖收集器')
        module = importlib.import_module('nailong.core.review_evidence')
        return module.ReviewCoverageCollector(paths, fingerprint_before='input-v1', **kwargs)

    def finish(self, collector, **kwargs):
        args = dict(fingerprint_after='input-v1', completed=True,
            task_revision=2, evidence_id='review-result', artifact_ref='review:turn')
        args.update(kwargs)
        return collector.finish(**args)

    def test_same_version_pages_cover_only_after_both_are_consumed(self):
        collector = self.collector()
        collector.consume_request([page('r1', 'abc')])
        partial = self.finish(collector)
        self.assertEqual(partial['status'], 'unverified')
        self.assertEqual(partial['files'][0]['missing_ranges'], [[3, 6]])
        collector.consume_request([page('r1', 'abc'), page('r2', 'def', offset=3)])
        result = self.finish(collector)
        self.assertEqual(result['status'], 'reviewed')
        self.assertEqual(result['files'][0]['covered_chars'], 6)
        self.assertEqual(result['files'][0]['missing_ranges'], [])
        self.assertEqual(result['evidence']['kind'], 'review')
        self.assertEqual(result['evidence']['source'], 'runtime')
        self.assertEqual(result['evidence']['input_fingerprint'], 'input-v1')
        task = snapshot()
        task['acceptance'][0].update(status='passed', evidence_ids=['review-result'])
        task['evidence'] = [result['evidence']]
        self.assertEqual(build_delivery_report(task, current_input_fingerprint='input-v1')['status'], 'reviewed')

    def test_overlapping_pages_do_not_add_duplicate_characters(self):
        collector = self.collector()
        collector.consume_request([page('r1', 'abcd'), page('r2', 'cdef', offset=2)])
        result = self.finish(collector)
        self.assertEqual(result['status'], 'reviewed')
        self.assertEqual(result['files'][0]['covered_chars'], 6)

    def test_duplicate_call_id_cannot_fill_another_page(self):
        collector = self.collector()
        collector.consume_request([page('same', 'abc')])
        collector.consume_request([page('same', 'def', offset=3)])
        result = self.finish(collector)
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['files'][0]['covered_chars'], 3)
        self.assertIsNone(result['evidence'])

    def test_historical_calls_do_not_prove_current_input(self):
        collector = self.collector(excluded_call_ids={'old'})
        collector.consume_request([page('old', 'abcdef')])
        self.assertEqual(self.finish(collector)['status'], 'unverified')
        collector.consume_request([page('new', 'abcdef')])
        self.assertEqual(self.finish(collector)['status'], 'reviewed')

    def test_model_claim_does_not_create_file_coverage(self):
        collector = self.collector()
        collector.consume_request([AIMessage(content='我完整审查了 a.py，没有任何问题')])
        self.assertEqual(self.finish(collector)['status'], 'unverified')

    def test_missing_page_is_not_replaced_by_read_complete_flag(self):
        collector = self.collector()
        collector.consume_request([page('tail', 'def', offset=3, read_complete=True, coverage='complete')])
        result = self.finish(collector)
        self.assertEqual(result['files'][0]['missing_ranges'], [[0, 3]])
        self.assertEqual(result['status'], 'unverified')

    def test_version_change_cannot_merge_old_and_new_halves(self):
        collector = self.collector()
        collector.consume_request([page('r1', 'abc'), page('r2', 'def', offset=3, version='file-v2')])
        result = self.finish(collector)
        self.assertEqual(result['status'], 'unverified')
        self.assertIsNone(result['evidence'])
        self.assertTrue(result['reasons'])

    def test_inconsistent_total_for_one_version_cannot_claim_complete(self):
        collector = self.collector()
        collector.consume_request([page('r1', 'abc'), page('r2', 'def', offset=3, total=7)])
        self.assertEqual(self.finish(collector)['status'], 'unverified')

    def test_failed_truncated_and_malformed_pages_do_not_count(self):
        cases = [
            page('r', 'abcdef', ok=False),
            page('r', 'abcdef', result_truncated=True),
            page('r', 'abcdef', content_offset=-1),
            page('r', 'abcdef', content_offset=True),
            page('r', 'abcdef', total_chars=True),
            page('r', 'abcdef', total=5),
            page('r', 'abcdef', version='unknown'),
            ToolMessage(name='read_file', tool_call_id='r', content='截断的 JSON'),
        ]
        for message in cases:
            with self.subTest(content=message.content):
                collector = self.collector()
                collector.consume_request([message])
                result = self.finish(collector)
                self.assertEqual(result['status'], 'unverified')
                self.assertIsNone(result['evidence'])

    def test_each_selected_file_requires_its_own_input(self):
        collector = self.collector(('a.py', 'b.py'))
        collector.consume_request([page('r1', 'abcdef')])
        self.assertEqual(self.finish(collector)['status'], 'unverified')
        collector.consume_request([page('r2', '', total=0, path='b.py')])
        self.assertEqual(self.finish(collector)['status'], 'reviewed')

    def test_empty_file_requires_a_successful_explicit_read(self):
        collector = self.collector()
        self.assertEqual(self.finish(collector)['status'], 'unverified')
        collector.consume_request([page('empty', '', total=0)])
        self.assertEqual(self.finish(collector)['status'], 'reviewed')

    def test_incomplete_selection_or_skipped_file_blocks_workflow_evidence(self):
        for kwargs in ({'selection_complete': False}, {'skipped_paths': ('secret.py',)}):
            with self.subTest(kwargs=kwargs):
                collector = self.collector(**kwargs)
                collector.consume_request([page('r1', 'abcdef')])
                self.assertEqual(self.finish(collector)['status'], 'unverified')
        collector = self.collector(tuple(f'f{i}.py' for i in range(21)))
        collector.consume_request([page(str(i), '', total=0, path=f'f{i}.py') for i in range(21)])
        self.assertEqual(self.finish(collector)['status'], 'unverified')

    def test_unchanged_independent_input_and_completed_flow_are_required(self):
        collector = self.collector()
        collector.consume_request([page('r1', 'abcdef')])
        for kwargs in ({'fingerprint_after': None}, {'fingerprint_after': 'unknown'},
                       {'fingerprint_after': 'input-v2'}, {'completed': False},
                       {'task_revision': True}, {'evidence_id': ''}):
            with self.subTest(kwargs=kwargs):
                result = self.finish(collector, **kwargs)
                self.assertEqual(result['status'], 'unverified')
                self.assertIsNone(result['evidence'])

    def test_outside_scope_page_cannot_satisfy_selected_file(self):
        collector = self.collector()
        collector.consume_request([page('other', 'abcdef', path='b.py')])
        self.assertEqual(self.finish(collector)['status'], 'unverified')

    def test_evidence_does_not_persist_original_file_or_model_prose(self):
        collector = self.collector()
        secret_body = 'opaque-source-body'
        collector.consume_request([page('r', secret_body, total=len(secret_body)),
            AIMessage(content='模型声称整个项目安全')])
        result = self.finish(collector)
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertEqual(result['status'], 'reviewed')
        self.assertNotIn(secret_body, encoded)
        self.assertNotIn('整个项目安全', encoded)

    def test_real_unicode_file_pages_use_absolute_character_offsets(self):
        from nailong.tools.files import FileSession
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a.py').write_text('甲乙\nγδ\n', encoding='utf-8', newline='\n')
            session = FileSession(root)
            first = session.read_file('a.py', limit=1)
            second = session.read_file('a.py', offset=first['next_offset'], limit=1)
            self.assertEqual(first['content_offset'], 0)
            self.assertEqual(second['content_offset'], 3)
            collector = self.collector()
            collector.consume_request([ToolMessage(name='read_file', tool_call_id='first',
                content=json.dumps(first, ensure_ascii=False))])
            self.assertEqual(self.finish(collector)['files'][0]['missing_ranges'], [[3, 6]])
            collector.consume_request([ToolMessage(name='read_file', tool_call_id='second',
                content=json.dumps(second, ensure_ascii=False))])
            result = self.finish(collector)
            self.assertEqual(result['status'], 'reviewed')
            self.assertEqual(result['files'][0]['covered_chars'], 6)

    def test_real_same_line_character_paging_does_not_skip_middle(self):
        from nailong.tools.files import FileSession
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a.py').write_text('abcdef', encoding='utf-8', newline='\n')
            session = FileSession(root)
            first = session.read_file('a.py', max_chars=3)
            second = session.read_file('a.py', offset=first['next_offset'],
                char_offset=first['next_char_offset'], max_chars=3)
            collector = self.collector()
            for identifier, body in [('first', first), ('second', second)]:
                collector.consume_request([ToolMessage(name='read_file', tool_call_id=identifier,
                    content=json.dumps(body))])
            self.assertEqual(self.finish(collector)['status'], 'reviewed')

    def test_unseen_failed_request_cannot_be_replaced_by_cumulative_tool_flag(self):
        collector = self.collector()
        # The first page existed at tool execution, but a provider failure means
        # the caller never submits that request to the successful-request collector.
        collector.consume_request([page('r2', 'def', offset=3, read_complete=True)])
        self.assertEqual(self.finish(collector)['files'][0]['missing_ranges'], [[0, 3]])

    def test_text_block_envelope_and_fresh_retry_preserve_actual_consumption(self):
        collector = self.collector()
        collector.consume_request([page('failed', '', ok=False)])
        body = json.loads(page('fresh', 'abcdef').content)
        collector.consume_request([ToolMessage(name='read_file', tool_call_id='fresh',
            content=[{'type': 'text', 'text': json.dumps({'ok': True, 'data': body})}])])
        self.assertEqual(self.finish(collector)['status'], 'reviewed')

    def test_error_status_or_missing_absolute_offset_never_counts_as_full(self):
        missing = json.loads(page('missing', 'abcdef').content)
        del missing['content_offset']
        for message in [ToolMessage(name='read_file', tool_call_id='missing', content=json.dumps(missing)),
                        ToolMessage(name='read_file', tool_call_id='error', status='error',
                            content=page('error', 'abcdef').content)]:
            with self.subTest(call_id=message.tool_call_id):
                collector = self.collector()
                collector.consume_request([message])
                self.assertEqual(self.finish(collector)['status'], 'unverified')

    def test_empty_selection_and_unknown_before_fingerprint_do_not_review(self):
        self.assertEqual(self.finish(self.collector(()))['status'], 'unverified')
        module = importlib.import_module('nailong.core.review_evidence')
        collector = module.ReviewCoverageCollector(['a.py'], fingerprint_before=None)
        collector.consume_request([page('r', 'abcdef')])
        self.assertEqual(self.finish(collector)['status'], 'unverified')

    def test_redaction_expansion_cannot_bridge_an_unread_character_gap(self):
        collector = self.collector()
        # A one-character secret became seven displayed characters. Counting the
        # replacement's length would incorrectly cover original positions 1..7.
        collector.consume_request([page('redacted', '[密钥已隐藏]', total=10),
            page('tail', 'xyz', offset=7, total=10)])
        result = self.finish(collector)
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['files'][0]['missing_ranges'], [[0, 7]])

    def test_explicit_content_transformation_never_proves_raw_file_coverage(self):
        for flag in ('content_redacted', 'content_transformed'):
            with self.subTest(flag=flag):
                collector = self.collector()
                collector.consume_request([page('r', 'abcdef', **{flag: True})])
                self.assertEqual(self.finish(collector)['status'], 'unverified')


class ManagedDeliveryTests(unittest.TestCase):
    def evidence(self, **kwargs):
        return {'id': 'proof', 'kind': 'review', 'source': 'runtime', 'status': 'passed',
            'task_revision': 2, 'paths': ['a.py'], 'input_fingerprint': 'input-v1',
            'coverage': 'complete', 'summary': '已完成静态流程覆盖', 'artifact_ref': 'review:turn', **kwargs}

    def proven_task(self, **kwargs):
        task = snapshot()
        task['acceptance'][0].update(status='passed', evidence_ids=['proof'])
        task['evidence'] = [self.evidence(**kwargs)]
        return task

    def test_model_unknown_stale_and_partial_evidence_never_prove_review(self):
        cases = [{'source': 'model'}, {'input_fingerprint': 'unknown'},
            {'task_revision': 1}, {'coverage': 'partial'}, {'paths': ['other.py']}]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                report = build_delivery_report(self.proven_task(**overrides), current_input_fingerprint='input-v1')
                self.assertNotIn(report['status'], {'verified', 'reviewed'})

    def test_unknown_current_input_does_not_fall_back_to_stored_fingerprint(self):
        task = self.proven_task()
        task['input_fingerprint'] = 'input-v1'
        self.assertEqual(build_delivery_report(task)['status'], 'unverified')

    def test_configured_execution_is_separate_from_goal_coverage(self):
        task = self.proven_task(kind='test')
        task['acceptance'][0].update(id='verify:test', kind='test')
        report = build_delivery_report(task, current_input_fingerprint='input-v1')
        self.assertEqual(report['configured_verification']['status'], 'verified')
        self.assertEqual(report['goal_coverage']['status'], 'unverified')
        self.assertEqual(report['status'], 'unverified')

    def test_failed_known_subset_is_visible_but_denied_is_not_a_test_failure(self):
        failed = build_delivery_report(self.proven_task(status='failed', coverage='partial'),
            current_input_fingerprint='input-v1')
        self.assertEqual(failed['status'], 'failed')
        denied = build_delivery_report(self.proven_task(status='denied', input_fingerprint=''),
            current_input_fingerprint=None)
        self.assertEqual(denied['status'], 'unverified')
        self.assertFalse(denied['failed'])
        self.assertTrue(denied['pending'][0]['reasons'])

    def test_waiver_is_an_explicit_user_decision_and_never_counts_as_passed(self):
        task = self.proven_task(kind='manual', source='user')
        task['acceptance'][0]['status'] = 'waived'
        report = build_delivery_report(task, current_input_fingerprint='input-v1')
        self.assertEqual(report['status'], 'unverified')
        self.assertEqual([row['id'] for row in report['waived']], ['review:scope'])
        self.assertFalse(report['satisfied'])

    def test_report_does_not_mutate_snapshot_or_render_original_evidence(self):
        task = self.proven_task(summary='RAW_EVIDENCE_SENTINEL', artifact_ref='COMMAND_SENTINEL')
        before = copy.deepcopy(task)
        report = build_delivery_report(task, current_input_fingerprint='input-v1')
        rendered = render_delivery_report(report)
        self.assertEqual(task, before)
        self.assertNotIn('RAW_EVIDENCE_SENTINEL', rendered)
        self.assertNotIn('COMMAND_SENTINEL', rendered)


if __name__ == '__main__':
    unittest.main()
