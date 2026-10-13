"""Request projections avoid duplicating visible user requirements, without loss."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from config import Settings
from nailong.core.context import UsageCalibration
from nailong.core.context_runtime import ContextLimitExceeded, ContextManagerMiddleware
from nailong.core.history_archive import HistoryArchive
from nailong.core.model import HarnessChatDeepSeek
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_context import render_task_context
from nailong.core.task_history import TaskContextHistory
from nailong.core.task_state import TaskStore


@tool
def read_task_context(reference: str) -> str:
    """Read archived requirements."""
    return reference


def projected(message):
    text = message.content
    return json.JSONDecoder().raw_decode(text[text.index('{"'):])[0]


class TaskProjectionEfficiencyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'project'
        self.root.mkdir()
        self.store = ProjectSessionStore(self.root, base_dir=Path(temporary.name) / 'runtime')
        self.tasks = TaskStore(self.store)
        self.archive = HistoryArchive(self.store.root / 'archive')
        self.history = TaskContextHistory(self.archive, 'one', self.root)
        self.settings = Settings('fixture-key', 'https://example.invalid', 'deepseek-flash',
                                 self.root, reasoning_effort='max')
        self.model = HarnessChatDeepSeek(api_key='fixture-key', base_url='https://example.invalid',
                                        model='deepseek-flash', reasoning_effort='max', max_tokens=131072)
        self.manager = ContextManagerMiddleware(self.settings, [read_task_context], 'BASE',
            'chat', 'one', self.store, self.archive, {}, UsageCalibration(),
            requested_output=131072, task_snapshot_provider=self.tasks.snapshot,
            task_history=self.history)

    def prepare(self, messages):
        return self.manager._prepare_request(ModelRequest(model=self.model, messages=messages,
            tools=[read_task_context], system_message=SystemMessage(content='BASE')))

    def test_trimmed_objective_references_original_requirement_instead_of_repeating_it(self):
        original = '  保留原接口，先提交计划。\n'
        task = self.tasks.begin('one', original)
        reference = self.history.save(task)
        text = render_task_context(task, history_reference=reference)
        view = projected(HumanMessage(content=text))
        self.assertEqual(view['objective'], {'requirement_id': 'r000001'})
        self.assertEqual(view['active_requirements'][0]['text'], original)
        self.assertEqual(task, self.tasks.snapshot('one'))

    def test_actual_sdk_request_has_one_original_and_full_archive_remains_readable(self):
        original = '  只读调查；保留所有公开接口。\n' + '具体约束。' * 120
        before = self.tasks.begin('one', original)
        user = HumanMessage(content=original)
        request, report = self.prepare([user])
        view = projected(request.messages[-1])
        row = view['active_requirements'][0]
        self.assertNotIn('text', row)
        self.assertEqual(row['message_index'], 0)
        self.assertEqual(request.messages[0].content, original)
        payload = self.model._get_request_payload(request.messages, **request.model_settings)
        self.assertEqual(sum(m.get('content', '').count(original) for m in payload['messages']), 1)
        self.assertEqual(payload['max_tokens'], 131072)
        self.assertEqual(payload['reasoning_effort'], 'max')
        loaded = self.history.read(report['task_projection']['history_reference'],
                                   section='requirements', record_id='r000001')
        self.assertEqual(json.loads(loaded['content'])[0]['text'], original)
        self.assertEqual(self.tasks.snapshot('one'), before)

    def test_only_exact_visible_user_text_can_replace_requirement_body(self):
        original = '原始用户要求：不得改接口'
        self.tasks.begin('one', original)
        for message in (HumanMessage(content=original + '，这是别的请求'),
                        HumanMessage(content=original, name='runtime-data'),
                        HumanMessage(content=original, additional_kwargs={'nailong_task_context': 1})):
            with self.subTest(message=message):
                request, _ = self.prepare([message])
                self.assertEqual(projected(request.messages[-1])['active_requirements'][0]['text'], original)

    def test_two_visible_phases_keep_user_history_and_requirement_status(self):
        first = '先只读调查，等待实施授权'
        latest = '现在授权实施，保留前述公开接口'
        self.tasks.begin('one', first)
        before = self.tasks.begin('one', latest)
        request, _ = self.prepare([HumanMessage(content=first), HumanMessage(content=latest)])
        rows = projected(request.messages[-1])['active_requirements']
        self.assertEqual([(r['id'], r['status'], r['message_index']) for r in rows],
                         [('r000001', 'active', 0), ('r000002', 'active', 1)])
        self.assertEqual([m.content for m in request.messages[:-1]], [first, latest])
        self.assertEqual(self.tasks.snapshot('one'), before)

    def test_missing_older_user_message_keeps_its_full_requirement(self):
        first = '较早的约束必须仍可看到'
        latest = '继续增加日志'
        self.tasks.begin('one', first)
        self.tasks.begin('one', latest)
        request, _ = self.prepare([HumanMessage(content=latest)])
        rows = projected(request.messages[-1])['active_requirements']
        self.assertEqual(rows[0]['text'], first)
        self.assertEqual(rows[1]['message_index'], 0)

    def test_repeated_wrapping_preserves_indices_reasoning_and_tool_exchange(self):
        original = '调查并保持接口'
        self.tasks.begin('one', original)
        messages = [HumanMessage(content=original), AIMessage(content='',
            tool_calls=[{'id': 'read-1', 'name': 'read_task_context', 'args': {'reference': 'ref'}}],
            additional_kwargs={'reasoning_content': 'required provider reasoning'}),
            ToolMessage(content='完整工具结果', tool_call_id='read-1')]
        saved = copy.deepcopy(messages)
        first, _ = self.prepare(messages)
        again, _ = self.manager._prepare_request(first)
        self.assertEqual(again.messages[:-1], saved)
        self.assertEqual(first.messages, again.messages)
        self.assertEqual(projected(again.messages[-1])['active_requirements'][0]['message_index'], 0)
        self.assertEqual(messages, saved)

    def test_compaction_rebuilds_requirements_before_admitting_checkpoint_update(self):
        original = '不得更改接口。' + '原始用户约束。' * 750
        before = self.tasks.begin('one', original)
        directory = self.root / '.nailong'
        directory.mkdir(exist_ok=True)
        (directory / 'settings.json').write_text(json.dumps({
            'context_windows': {'deepseek-flash': 15000}}))
        messages = [HumanMessage(id='u0', content=original),
                    AIMessage(id='a0', content='资料' * 3000)]
        for index in range(1, 8):
            messages += [HumanMessage(id=f'u{index}', content=f'第{index}轮继续调查'),
                         AIMessage(id=f'a{index}', content='资料' * 3000)]
        saved = copy.deepcopy(messages)
        try:
            update = self.manager.before_model({'messages': messages}, None)
        except ContextLimitExceeded:
            self.assertIn(original, self.manager._preflight_view[-1].content)
        else:
            retained = [m for m in update['messages'] if m.type != 'remove'] if update else messages
            request, report = self.prepare(retained)
            self.assertLessEqual(report['estimated_tokens'], report['input_limit'])
            self.assertTrue(any(original in str(m.content) for m in request.messages))
            self.assertEqual(self.manager._preflight_view, request.messages)
        self.assertEqual(messages, saved)
        self.assertEqual(self.tasks.snapshot('one'), before)

    def test_history_system_message_uses_request_messages_index_without_ambiguous_label(self):
        original = '保留有效约束'
        self.tasks.begin('one', original)
        request, _ = self.prepare([SystemMessage(content='历史系统资料'), HumanMessage(content=original)])
        row = projected(request.messages[-1])['active_requirements'][0]
        self.assertEqual(row['message_index'], 1)
        self.assertEqual(request.messages[row['message_index']].content, original)
        self.assertIn('messages', request.messages[-1].content)

    def test_whitespace_distinct_user_requests_keep_exact_latest_provenance(self):
        first, latest = '    return 1\n', 'return 1\n'
        self.tasks.begin('one', first)
        before = self.tasks.begin('one', latest)
        request, _ = self.prepare([HumanMessage(content=first), HumanMessage(content=latest)])
        view = projected(request.messages[-1])
        self.assertEqual(view['latest_request'], {'requirement_id': 'r000002'})
        self.assertEqual(view['objective'], before['objective'])
        self.assertEqual(request.messages[0].content, first)
        self.assertEqual(request.messages[1].content, latest)
