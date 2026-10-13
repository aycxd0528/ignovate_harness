import json
import tempfile
import unittest
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from nailong.core.compact import compact_messages
from nailong.core.history_archive import HistoryArchive


class ReasoningCompactionTests(unittest.TestCase):
    def test_dropped_reasoning_blocks_cannot_inflate_real_compaction_savings(self):
        # DeepSeek drops non-text assistant blocks. Counting their repr plus the
        # replayed field could pretend a below-20% saving meets the soft gate.
        messages = [HumanMessage(content='old task'),
            AIMessage(content=[{'type': 'text', 'text': 'done'},
                               {'type': 'reasoning', 'reasoning': 'r' * 6000}],
                      additional_kwargs={'reasoning_content': 'r' * 6000}),
            HumanMessage(content='current task'), AIMessage(content='c' * 26000)]
        with tempfile.TemporaryDirectory() as directory:
            result = compact_messages(messages, level='L2', keep_turns=1,
                archive=HistoryArchive(Path(directory).resolve()), thread_id='one')
        self.assertFalse(result.compacted)
        self.assertEqual(result.reason, 'insufficient_savings')
        self.assertEqual(result.messages, messages)

    def test_soft_compaction_counts_discarded_reasoning_as_real_savings(self):
        # Dropping reasoning from the savings estimate would refuse useful L2.
        messages = []
        for i in range(7):
            messages += [HumanMessage(id=f'u{i}', content=f'request {i}'),
                         AIMessage(id=f'a{i}', content='done',
                                   additional_kwargs={'reasoning_content': '分析' * 12000})]
        with tempfile.TemporaryDirectory() as directory:
            result = compact_messages(messages, level='L2', keep_turns=1,
                archive=HistoryArchive(Path(directory).resolve()), thread_id='one')
        self.assertTrue(result.compacted)
        self.assertGreater(result.before_tokens, 160000)
        self.assertIn(messages[-1], result.messages)
        self.assertEqual(messages[-1].additional_kwargs['reasoning_content'], '分析' * 12000)

    def test_discarded_assistant_content_and_reasoning_have_immutable_archive_refs(self):
        # An assistant must not disappear from full archive merely because its
        # content is summarized; the provider reasoning is part of that message.
        with tempfile.TemporaryDirectory() as directory:
            archive = HistoryArchive(Path(directory).resolve())
            old = AIMessage(id='old-ai', content='unverified conclusion',
                additional_kwargs={'reasoning_content': 'old private reasoning'})
            result = compact_messages([HumanMessage(content='old request'), old,
                HumanMessage(content='current request')], level='L2', keep_turns=1,
                hard=True, archive=archive, thread_id='one')
            summary = next(m for m in result.messages if m.additional_kwargs.get('nailong_compact_summary'))
            records = [archive.load('one', ref) for ref in
                       summary.additional_kwargs['nailong_compact_state']['evidence']]
            assistant = next((r for r in records if r['kind'] == 'ai'), None)
            self.assertIsNotNone(assistant)
            self.assertEqual(assistant['message']['data']['content'], 'unverified conclusion')
            self.assertEqual(assistant['message']['data']['additional_kwargs']['reasoning_content'],
                             'old private reasoning')

    def test_assistant_archive_failure_prevents_destructive_compaction(self):
        class FailingArchive:
            def save(self, thread_id, message, arguments=None):
                if message.type == 'ai':
                    raise ValueError('assistant archive unavailable')
                return 'ref'
            def read(self, thread_id, reference, max_chars=1):
                return {'archive_truncated': False}
        messages = [HumanMessage(content='old'), AIMessage(content='reasoned answer'),
                    HumanMessage(content='current')]
        with self.assertRaisesRegex(ValueError, 'assistant archive unavailable'):
            compact_messages(messages, level='L2', keep_turns=1, hard=True,
                             archive=FailingArchive(), thread_id='one')

    def test_incomplete_assistant_archive_preserves_original_messages(self):
        # Metadata above the archive limit must not silently lose reasoning.
        with tempfile.TemporaryDirectory() as directory:
            archive = HistoryArchive(Path(directory).resolve())
            messages = [HumanMessage(content='old'), AIMessage(content='',
                additional_kwargs={'reasoning_content': 'r' * (3 * 1024 * 1024)}),
                HumanMessage(content='current')]
            result = compact_messages(messages, level='L2', keep_turns=1, hard=True,
                                      archive=archive, thread_id='one')
            self.assertFalse(result.compacted)
            self.assertEqual(result.messages, messages)

    def test_tool_clearing_keeps_test_failure_even_when_shell_tail_says_done(self):
        failure = 'Ran 4 tests in 0.12s\n\nFAILED (failures=1)\n'
        messages = [HumanMessage(content='verify'),
            AIMessage(content='', tool_calls=[{'id': 'old', 'name': 'run_command',
                'args': {'command': 'python -m unittest; echo done'}, 'type': 'tool_call'}]),
            ToolMessage(id='old-result', tool_call_id='old', name='run_command',
                content=json.dumps({'ok': True, 'exit_code': 0, 'output':
                    failure + 'later shell output\n' * 800 + 'done'})),
            AIMessage(content='', tool_calls=[{'id': 'new', 'name': 'read_file',
                'args': {'path': 'x'}, 'type': 'tool_call'}]),
            ToolMessage(tool_call_id='new', name='read_file', content='current')]
        result = compact_messages(messages, level='L1', min_gain=0)
        body = next(m.content for m in result.messages if m.id == 'old-result')
        record = json.loads(body.split('\n', 1)[1])
        self.assertEqual(record['exit_code'], 0)
        self.assertTrue(record.get('test_output', {}).get('failure_observed'))
        self.assertEqual(record['test_output']['status'], 'reported_failure')

    def test_l2_rendered_action_excerpt_retains_observed_test_failure(self):
        # The action's fixed excerpt budget must not prefer a successful shell
        # wrapper/path/long tail over the failure observed earlier in its output.
        payload = {'ok': True, 'exit_code': 0, 'path': 'p' * 500,
                   'output': 'Ran 4 tests in 0.12s\nFAILED (failures=1)\n' + 'tail' * 500}
        messages = [HumanMessage(content='old task'),
            AIMessage(content='', tool_calls=[{'id': 'test', 'name': 'run_command',
                'args': {}, 'type': 'tool_call'}]),
            ToolMessage(name='run_command', tool_call_id='test', content=json.dumps(payload)),
            HumanMessage(content='current task')]
        with tempfile.TemporaryDirectory() as directory:
            result = compact_messages(messages, level='L2', keep_turns=1, hard=True,
                archive=HistoryArchive(Path(directory).resolve()), thread_id='one')
        text = next(m.content for m in result.messages if m.additional_kwargs.get('nailong_compact_summary'))
        self.assertIn('reported_failure', text)


if __name__ == '__main__':
    unittest.main()
