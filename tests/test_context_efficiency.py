"""Cached accounting must retain the complete-request and archive contracts."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from threading import Lock

from langchain.agents.middleware import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, Tool, tool
from pydantic import PrivateAttr

from config import Settings
from nailong.core import context
from nailong.core.context_runtime import ContextLimitExceeded, ContextManagerMiddleware
from nailong.core.history_archive import HistoryArchive
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_history import TaskContextHistory
from nailong.core.task_state import TaskStore


class EstimateCacheTests(unittest.TestCase):
    def cache(self, **options):
        factory = getattr(context, 'RequestEstimateCache', None)
        self.assertTrue(callable(factory), 'A bounded request estimate cache is required')
        return factory(**options)

    def test_category_rounding_and_calibration_survive_fragment_reuse(self):
        cache = self.cache()
        messages = [HumanMessage(content='a'), HumanMessage(content='bc'), HumanMessage(content='d')]
        first = context.request_report(messages, cache=cache)
        second = context.request_report(messages, cache=cache, factor=1.5)
        self.assertEqual(first['categories']['history'], {'characters': 4, 'tokens': 1, 'raw_tokens': 1})
        self.assertEqual(second['categories']['history'], {'characters': 4, 'tokens': 2, 'raw_tokens': 1})
        self.assertEqual(second['raw_estimated_tokens'], first['raw_estimated_tokens'])
        self.assertGreater(cache.snapshot()['text_hits'], 0)
        messages[1].content = '中文'
        changed = context.request_report(messages, cache=cache)
        self.assertEqual(changed['categories']['history']['raw_tokens'], 3)

    def test_nested_schema_mutation_and_filtered_tool_set_invalidate_cache(self):
        cache = self.cache()
        definition = {'type': 'function', 'function': {'name': 'probe', 'parameters': {
            'type': 'object', 'properties': {'query': {'type': 'string', 'description': '小'}}}}}
        first = context.request_report([], tools=[definition], cache=cache)
        context.request_report([], tools=[definition], cache=cache)
        self.assertGreater(cache.snapshot()['schema_hits'], 0)
        definition['function']['parameters']['properties']['query']['description'] = '大' * 201
        changed = context.request_report([], tools=[definition], cache=cache)
        self.assertEqual(changed['categories']['tool_definitions']['characters'] -
                         first['categories']['tool_definitions']['characters'], 200)
        self.assertEqual(changed['categories']['tool_definitions']['raw_tokens'] -
                         first['categories']['tool_definitions']['raw_tokens'], 200)
        empty = context.request_report([], tools=[], cache=cache)
        self.assertEqual(empty['categories']['tool_definitions']['raw_tokens'], 0)

    def test_base_tool_description_and_schema_reassignment_are_not_stale(self):
        cache = self.cache()

        @tool
        def probe(query: str) -> str:
            """Read a query."""
            return query

        first = context.request_report([], tools=[probe], cache=cache)
        context.request_report([], tools=[probe], cache=cache)
        self.assertGreater(cache.snapshot()['schema_hits'], 0)
        probe.description += '说明' * 100
        changed = context.request_report([], tools=[probe], cache=cache)
        self.assertGreaterEqual(changed['estimated_tokens'] - first['estimated_tokens'], 200)
        probe.args_schema = {'type': 'object', 'properties': {'extra': {'type': 'string', 'description': '新' * 500}}}
        again = context.request_report([], tools=[probe], cache=cache)
        self.assertGreater(again['estimated_tokens'], changed['estimated_tokens'])

    def test_same_message_id_call_arguments_and_metadata_can_change(self):
        cache = self.cache()
        ai = AIMessage(id='same', content='', tool_calls=[{'name': 'read_file', 'id': 'r', 'args': {'path': 'a'}}])
        result = ToolMessage(content='结果', tool_call_id='r')
        first = context.request_report([ai, result], cache=cache)
        ai.tool_calls[0]['name'] = 'load_skill'
        ai.tool_calls[0]['args']['path'] = '中' * 500
        changed = context.request_report([ai, result], cache=cache)
        self.assertEqual(changed['categories']['loaded_skills']['characters'], 2)
        self.assertEqual(changed['categories']['tool_results']['characters'], 0)
        self.assertGreater(changed['estimated_tokens'] - first['estimated_tokens'], 490)
        human = HumanMessage(id='same', content='abcd')
        context.request_report([human], cache=cache)
        human.additional_kwargs['nailong_pin'] = 'plan'
        pinned = context.request_report([human], cache=cache)
        self.assertEqual(pinned['categories']['fixed_memory']['characters'], 4)
        self.assertEqual(pinned['categories']['history']['characters'], 0)

    def test_custom_tool_format_mutation_is_counted_without_function_schema(self):
        cache = self.cache()
        custom = Tool(name='code', description='Execute code', func=lambda text: text,
                      metadata={'type': 'custom_tool', 'format': {'type': 'text'}})
        first = context.request_report([], tools=[custom], cache=cache)
        context.request_report([], tools=[custom], cache=cache)
        custom.metadata['format']['grammar'] = '文' * 300
        changed = context.request_report([], tools=[custom], cache=cache)
        self.assertGreaterEqual(changed['estimated_tokens'] - first['estimated_tokens'], 300)

    def test_cache_is_bounded_and_oversized_fragments_are_still_counted(self):
        cache = self.cache(max_entries=4, max_characters=100)
        for i in range(30):
            context.request_report([HumanMessage(content=str(i) + '中' * 20)], cache=cache)
        stats = cache.snapshot()
        self.assertLessEqual(stats['text_entries'], 4)
        self.assertLessEqual(stats['text_characters'], 100)
        result = context.request_report([HumanMessage(content='中' * 500)], cache=cache)
        self.assertEqual(result['categories']['history']['raw_tokens'], 500)
        self.assertLessEqual(cache.snapshot()['text_characters'], 100)

    def test_dynamic_schema_provider_is_converted_instead_of_cached(self):
        cache = self.cache()

        class DynamicTool(BaseTool):
            name: str = 'dynamic'
            description: str = 'Dynamic schema'
            _reads: int = PrivateAttr(default=0)

            @property
            def tool_call_schema(self):
                self._reads += 1
                description = '固定' if self._reads % 4 == 1 else '中' * self._reads
                return {'type': 'object', 'properties': {'q': {'type': 'string', 'description': description}}}

            def _run(self, **kwargs):
                return 'unused'

        dynamic = DynamicTool()
        first = context.request_report([], tools=[dynamic], cache=cache)
        second = context.request_report([], tools=[dynamic], cache=cache)
        self.assertGreater(second['estimated_tokens'], first['estimated_tokens'])


class ContextEfficiencyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'project'
        self.root.mkdir()
        self.sessions = ProjectSessionStore(self.root, base_dir=Path(temporary.name) / 'data')
        self.archive = HistoryArchive(self.sessions.root / 'history-results')
        self.settings = Settings('placeholder', 'https://example.invalid', 'private', self.root)
        self.manager = ContextManagerMiddleware(self.settings, [], 'BASE', 'chat', 'one',
            self.sessions, self.archive, {}, context.UsageCalibration())
        self.model = FakeMessagesListChatModel(responses=[AIMessage(content='done')])

    def configure(self, threshold=150000, window=None):
        directory = self.root / '.nailong'
        directory.mkdir(exist_ok=True)
        directory.joinpath('settings.json').write_text(json.dumps({
            'context': {'soft_threshold_tokens': threshold}, 'context_windows': {'private': window}}))

    def request(self, messages, tools=()):
        return ModelRequest(model=self.model, messages=messages, tools=list(tools),
                            system_message=SystemMessage(content='BASE'))

    def test_phase_timings_and_cache_statistics_are_persisted(self):
        messages = [HumanMessage(content='读取项目')]
        self.manager.before_model({'messages': messages}, None)
        _, report = self.manager._prepare_request(self.request(messages))
        self.assertIn('performance', report)
        performance = report['performance']
        for stage in ('preflight', 'request'):
            self.assertGreaterEqual(performance[stage]['total_ms'], 0)
            for value in performance[stage]['durations_ms'].values():
                self.assertTrue(math.isfinite(value) and value >= 0)
        self.assertGreater(performance['cache']['text_hits'], 0)
        persisted = [row for row in self.sessions.read_events('one') if row['kind'] == 'context_request'][-1]
        self.assertEqual(persisted['data']['performance'], performance)

    def test_repeated_preflight_reuses_archive_and_changed_result_is_saved(self):
        result = ToolMessage(id='t', content='旧正文', tool_call_id='r', name='read_file')
        messages = [HumanMessage(content='read'), AIMessage(content='', tool_calls=[
            {'name': 'read_file', 'id': 'r', 'args': {'path': 'file.py'}}]), result]
        self.manager.before_model({'messages': messages}, None)
        self.manager.before_model({'messages': messages}, None)
        _, report = self.manager._prepare_request(self.request(messages))
        self.assertIn('performance', report)
        self.assertEqual(report['performance']['preflight']['archive_saved'], 0)
        self.assertEqual(report['performance']['preflight']['archive_reused'], 1)
        result.content = '新正文'
        self.manager.before_model({'messages': messages}, None)
        _, report = self.manager._prepare_request(self.request(messages))
        self.assertEqual(report['performance']['preflight']['archive_saved'], 1)
        records = [json.loads(path.read_text()) for path in self.archive.root.rglob('hist_*.json')]
        self.assertEqual({row['message']['data']['content'] for row in records}, {'旧正文', '新正文'})

    def test_final_guard_rechecks_changed_message_and_window_after_preflight(self):
        messages = [HumanMessage(id='same', content='small')]
        self.configure(window=10000)
        self.manager.before_model({'messages': messages}, None)
        self.manager._prepare_request(self.request(messages))
        messages[0].content = '中' * 10000
        with self.assertRaises(ContextLimitExceeded):
            self.manager._prepare_request(self.request(messages))
        messages[0].content = 'small'
        self.configure(window=64)
        with self.assertRaises(ContextLimitExceeded):
            self.manager._prepare_request(self.request(messages))

    def test_same_revision_task_progress_and_archive_are_refreshed(self):
        tasks = TaskStore(self.sessions)
        original = tasks.begin('one', '修复入口')
        self.manager.task_snapshot_provider = tasks.snapshot
        self.manager.task_history = TaskContextHistory(self.archive, 'one', self.root)
        tools = [{'type': 'function', 'function': {'name': 'read_task_context'}}]
        messages = [HumanMessage(content='开始')]
        first, _ = self.manager._prepare_request(self.request(messages, tools))
        changed = copy.deepcopy(original)
        changed['progress'] = '已经定位失败原因'
        tasks._write('one', changed)
        second, _ = self.manager._prepare_request(self.request(messages, tools))
        self.assertIn('已经定位失败原因', second.messages[-1].content)
        self.assertNotEqual(first.messages[-1].content, second.messages[-1].content)
        self.assertEqual(tasks.snapshot('one')['revision'], original['revision'])

    def test_no_gain_compaction_is_skipped_until_content_or_policy_changes(self):
        self.configure(threshold=100)
        messages = [HumanMessage(id='same', content='中' * 120)]
        self.manager.before_model({'messages': messages}, None)
        self.manager.before_model({'messages': messages}, None)
        _, report = self.manager._prepare_request(self.request(messages))
        self.assertIn('performance', report)
        self.assertEqual(report['performance']['preflight']['compaction_attempts'], 0)
        self.assertTrue(report['performance']['preflight']['compaction_skipped'])
        messages[0].content = '文' * 120
        self.manager.before_model({'messages': messages}, None)
        _, report = self.manager._prepare_request(self.request(messages))
        self.assertGreater(report['performance']['preflight']['compaction_attempts'], 0)
        self.manager.calibration.observe('private', 'example.invalid', 100, 200)
        self.manager.before_model({'messages': messages}, None)
        _, report = self.manager._prepare_request(self.request(messages))
        self.assertGreater(report['performance']['preflight']['compaction_attempts'], 0)
        self.configure(threshold=110)
        self.manager.before_model({'messages': messages}, None)
        _, report = self.manager._prepare_request(self.request(messages))
        self.assertGreater(report['performance']['preflight']['compaction_attempts'], 0)

    def test_cached_archive_is_validated_again_before_destructive_clearing(self):
        messages = [HumanMessage(content='read')]
        for i in range(2):
            messages.extend([AIMessage(content='', tool_calls=[
                {'name': 'read_file', 'id': str(i), 'args': {'path': 'file.py'}}]),
                ToolMessage(name='read_file', content='中' * 1000, tool_call_id=str(i))])
        self.manager.before_model({'messages': messages}, None)
        paths = list(self.archive.root.rglob('hist_*.json'))
        self.assertEqual(len(paths), 2)
        for path in paths:
            path.write_text('{}')
        self.configure(threshold=500)
        original = copy.deepcopy(messages)
        with self.assertRaises(ValueError):
            self.manager.before_model({'messages': messages}, None)
        self.assertEqual(messages, original)

    def test_deleted_cached_archive_is_recreated_before_clearing(self):
        messages = [HumanMessage(content='read')]
        for i in range(2):
            messages.extend([AIMessage(content='', tool_calls=[
                {'name': 'read_file', 'id': str(i), 'args': {'path': 'file.py'}}]),
                ToolMessage(name='read_file', content='中' * 1000, tool_call_id=str(i))])
        self.manager.before_model({'messages': messages}, None)
        for path in self.archive.root.rglob('hist_*.json'):
            path.unlink()
        self.configure(threshold=500)
        update = self.manager.before_model({'messages': messages}, None)
        cleared = [m for m in update['messages'] if isinstance(m, ToolMessage)
                   and m.additional_kwargs.get('nailong_tool_cleared')]
        self.assertTrue(cleared)
        for message in cleared:
            restored = self.archive.read('one', message.additional_kwargs['nailong_tool_reference'])
            self.assertEqual(restored['content'], '中' * 1000)

    def test_unchanged_uncompactable_input_still_hits_hard_guard(self):
        self.configure(threshold=100, window=200)
        messages = [HumanMessage(id='same', content='中' * 300)]
        for _ in range(2):
            with self.assertRaises(ContextLimitExceeded):
                self.manager.before_model({'messages': messages}, None)
        self.assertEqual(messages[0].content, '中' * 300)

    def test_failed_hard_compaction_reuses_original_checkpoint_fingerprint(self):
        self.configure(threshold=100, window=500)
        messages = [HumanMessage(content='中' * 1000), AIMessage(content='', tool_calls=[
            {'name': 'read_file', 'id': 'r', 'args': {'path': 'file.py'}}]),
            ToolMessage(name='read_file', content='文' * 1000, tool_call_id='r')]
        original = copy.deepcopy(messages)
        for _ in range(2):
            with self.assertRaises(ContextLimitExceeded):
                self.manager.before_model({'messages': messages}, None)
        event = [row for row in self.sessions.read_events('one') if row['kind'] == 'context_performance'][-1]
        self.assertTrue(event['data']['compaction_skipped'])
        self.assertEqual(event['data']['compaction_attempts'], 0)
        self.assertEqual(messages, original)

    def test_distinct_json_argument_types_have_distinct_eager_archives(self):
        call = AIMessage(content='', tool_calls=[{'name': 'read_file', 'id': 'r', 'args': {'value': 1}}])
        messages = [HumanMessage(content='read'), call,
                    ToolMessage(name='read_file', content='结果', tool_call_id='r')]
        self.manager.before_model({'messages': messages}, None)
        call.tool_calls[0]['args']['value'] = True
        self.manager.before_model({'messages': messages}, None)
        records = [json.loads(path.read_text()) for path in self.archive.root.rglob('hist_*.json')]
        self.assertEqual(len(records), 2)
        self.assertEqual(sorted(type(row['arguments']['value']).__name__ for row in records), ['bool', 'int'])

    def test_uncopyable_tool_artifact_does_not_break_successful_preservation(self):
        result = ToolMessage(name='read_file', content='结果', tool_call_id='r', artifact=Lock())
        messages = [HumanMessage(content='read'), AIMessage(content='', tool_calls=[
            {'name': 'read_file', 'id': 'r', 'args': {'path': 'file.py'}}]), result]
        try:
            self.manager.before_model({'messages': messages}, None)
        except TypeError:
            self.fail('An optional cache must not reject an already archived tool result')
        paths = list(self.archive.root.rglob('hist_*.json'))
        self.assertEqual(len(paths), 1)
        restored = self.archive.read('one', paths[0].stem)
        self.assertEqual(restored['content'], '结果')
        self.manager.before_model({'messages': messages}, None)


if __name__ == '__main__':
    unittest.main()
