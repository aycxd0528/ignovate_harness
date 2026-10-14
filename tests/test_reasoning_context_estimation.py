"""Estimate the reasoning field actually replayed on DeepSeek requests."""
import copy
import tempfile
import unittest
from pathlib import Path

from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from config import Settings
from nailong.core.context import RequestEstimateCache, UsageCalibration, request_report
from nailong.core.model import HarnessChatDeepSeek


class ReasoningContextEstimationTests(unittest.TestCase):
    def setUp(self):
        # Serialization is local; no provider invocation occurs in these tests.
        self.model = HarnessChatDeepSeek(api_key='fixture', api_base='https://example.invalid',
                                        model='deepseek-v4-flash')

    def test_large_replayed_reasoning_increases_the_actual_request_estimate(self):
        # Removing reasoning accounting must lose 54,054 estimated input tokens.
        reasoning = 'r' * 216_213
        assistant = AIMessage(content='done', additional_kwargs={'reasoning_content': reasoning})
        messages = [HumanMessage(content='next'), assistant]
        original = copy.deepcopy(messages)
        payload = self.model._get_request_payload(messages)
        self.assertEqual(payload['messages'][1]['reasoning_content'], reasoning)
        without_reasoning = request_report([HumanMessage(content='next'), AIMessage(content='done')])

        report = request_report(messages)

        self.assertEqual(report['raw_estimated_tokens'] - without_reasoning['raw_estimated_tokens'], 54_054)
        self.assertEqual(report['categories'].get('reasoning'),
                         {'characters': 216_213, 'tokens': 54_054, 'raw_tokens': 54_054})
        self.assertEqual(report['categories']['history'], {'characters': 8, 'tokens': 2, 'raw_tokens': 2})
        self.assertEqual(report['raw_estimated_tokens'], 54_069)
        self.assertEqual(messages, original)

    def test_cached_reasoning_is_recounted_after_same_message_id_changes(self):
        # A message-ID cache would return the stale short reasoning estimate.
        cache = RequestEstimateCache()
        message = AIMessage(id='same', content='done',
                            additional_kwargs={'reasoning_content': 'abcde中'})
        first = request_report([message], cache=cache)
        calibrated = request_report([message], cache=cache, factor=1.5)
        self.assertEqual(first['categories'].get('reasoning'), {'characters': 6, 'tokens': 3, 'raw_tokens': 3})
        self.assertEqual(calibrated['categories'].get('reasoning'), {'characters': 6, 'tokens': 5, 'raw_tokens': 3})
        self.assertEqual(calibrated['raw_estimated_tokens'], first['raw_estimated_tokens'])
        self.assertGreater(cache.snapshot()['text_hits'], 0)

        message.additional_kwargs['reasoning_content'] = '中' * 20
        changed = request_report([message], cache=cache)
        self.assertEqual(changed['categories'].get('reasoning'), {'characters': 20, 'tokens': 20, 'raw_tokens': 20})
        self.assertEqual(changed['raw_estimated_tokens'] - first['raw_estimated_tokens'], 17)

    def test_reasoning_fragments_round_once_per_category(self):
        # Per-message rounding would inflate these four ASCII chars to 3 tokens.
        messages = [AIMessage(content='', additional_kwargs={'reasoning_content': text})
                    for text in ('a', 'bc', 'd')]
        for cache in (None, RequestEstimateCache()):
            with self.subTest(cached=cache is not None):
                report = request_report(messages, cache=cache)
                self.assertEqual(report['categories'].get('reasoning'),
                                 {'characters': 4, 'tokens': 1, 'raw_tokens': 1})

    def test_dict_reasoning_matches_sdk_coercion_and_metadata_precedence(self):
        # Counting both dictionary locations or preferring the root double-counts
        # or substitutes reasoning that the actual serializer never sends.
        expected = request_report([AIMessage(content='done',
                                   additional_kwargs={'reasoning_content': 'abcd中'})])
        cases = [
            {'role': 'assistant', 'content': 'done', 'reasoning_content': 'abcd中'},
            {'type': 'ai', 'content': 'done', 'additional_kwargs': {'reasoning_content': 'abcd中'}},
            {'role': 'assistant', 'content': 'done', 'reasoning_content': 'unsent' * 1000,
             'additional_kwargs': {'reasoning_content': 'abcd中'}},
        ]
        for message in cases:
            with self.subTest(message_type=message.get('role', message.get('type')),
                              root_and_nested='reasoning_content' in message and 'additional_kwargs' in message):
                original = copy.deepcopy(message)
                self.assertEqual(self.model._get_request_payload([message])['messages'][0]['reasoning_content'], 'abcd中')
                self.assertEqual(request_report([message]), expected)
                self.assertEqual(message, original)
        self.assertEqual(expected['categories'].get('reasoning'), {'characters': 5, 'tokens': 2, 'raw_tokens': 2})

    def test_empty_invalid_and_non_assistant_reasoning_do_not_inflate_input(self):
        # The harness serializes only assistant string fields, including ''.
        baseline = request_report([AIMessage(content='done')])
        for value in ('', None, 42, True, [], {}, [{'type': 'text', 'text': 'unsent'}]):
            with self.subTest(value=value):
                message = AIMessage(content='done', additional_kwargs={'reasoning_content': value})
                payload = self.model._get_request_payload([message])['messages'][0]
                if isinstance(value, str):
                    self.assertEqual(payload['reasoning_content'], '')
                else:
                    self.assertNotIn('reasoning_content', payload)
                report = request_report([message])
                self.assertEqual(report['raw_estimated_tokens'], baseline['raw_estimated_tokens'])
                self.assertEqual(report['categories'].get('reasoning', {}).get('characters', 0), 0)
        nested_invalid = {'role': 'assistant', 'content': 'done', 'reasoning_content': 'unsent' * 1000,
                          'additional_kwargs': {'reasoning_content': 42}}
        self.assertNotIn('reasoning_content', self.model._get_request_payload([nested_invalid])['messages'][0])
        self.assertEqual(request_report([nested_invalid])['raw_estimated_tokens'], baseline['raw_estimated_tokens'])
        human = HumanMessage(content='next', additional_kwargs={'reasoning_content': 'unsent' * 1000})
        self.assertNotIn('reasoning_content', self.model._get_request_payload([human])['messages'][0])
        self.assertEqual(request_report([human]), request_report([HumanMessage(content='next')]))

    def test_assistant_reasoning_blocks_are_not_double_counted_in_history(self):
        # DeepSeek drops these output blocks; only the separately sent field
        # belongs in the request, and only text blocks become assistant content.
        reasoning = '中' * 1000
        content = [{'type': 'text', 'text': 'done'},
                   {'type': 'reasoning', 'reasoning': reasoning},
                   {'type': 'reasoning_content', 'reasoning_content': reasoning},
                   {'type': 'thinking', 'thinking': reasoning}]
        message = AIMessage(content=content, additional_kwargs={'reasoning_content': reasoning})
        payload = self.model._get_request_payload([message])['messages'][0]
        self.assertEqual(payload['content'], 'done')
        self.assertEqual(payload['reasoning_content'], reasoning)

        report = request_report([message])

        self.assertEqual(report['categories']['history'], {'characters': 4, 'tokens': 1, 'raw_tokens': 1})
        self.assertEqual(report['categories'].get('reasoning'), {'characters': 1000, 'tokens': 1000, 'raw_tokens': 1000})
        content_only = AIMessage(content=content)
        self.assertEqual(request_report([content_only]), request_report([AIMessage(content='done')]))
        self.assertEqual(message.content, content)

    def test_response_metadata_reasoning_is_not_an_extra_request_field(self):
        # Response diagnostics and differently named metadata never enter the wire.
        message = AIMessage(content='done', additional_kwargs={
            'reasoning_content': 'abcd', 'reasoning': 'unsent' * 1000},
            response_metadata={'reasoning_content': 'unsent' * 1000})
        self.assertEqual(self.model._get_request_payload([message])['messages'][0]['reasoning_content'], 'abcd')
        report = request_report([message])
        self.assertEqual(report['categories'].get('reasoning'), {'characters': 4, 'tokens': 1, 'raw_tokens': 1})
        self.assertEqual(report['categories']['history']['characters'], 4)

    def test_reasoning_preserves_exclusive_system_tool_and_task_categories(self):
        # Assigning reasoning to history or system parts would perturb existing
        # category values and give calibration a different total than the sum.
        parts = {'base_system': 'BASE', 'fixed_memory': '记忆', 'skill_catalog': 'CAT',
                 'tool_definitions': 'stale schema' * 1000}
        tool = {'type': 'function', 'function': {'name': 'probe', 'parameters': {'type': 'object'}}}
        messages = [HumanMessage(content='要求'),
                    AIMessage(content='', tool_calls=[{'name': 'load_skill', 'id': 's', 'args': {}}]),
                    ToolMessage(content='正文', tool_call_id='s'),
                    ToolMessage(content='结果', tool_call_id='f', name='read_file'),
                    HumanMessage(content='计划', additional_kwargs={'nailong_pin': 'plan'}),
                    HumanMessage(content='上下文', additional_kwargs={'nailong_task_context': 1})]
        baseline = request_report(messages, system_message='BASE记忆CAT!', tools=[tool], parts=parts, factor=1.5)
        messages[1].additional_kwargs['reasoning_content'] = '中文abcd'
        report = request_report(messages, system_message='BASE记忆CAT!', tools=[tool], parts=parts, factor=1.5)
        self.assertEqual({key: row for key, row in report['categories'].items() if key != 'reasoning'},
                         baseline['categories'])
        self.assertEqual(report['categories'].get('reasoning'), {'characters': 6, 'tokens': 5, 'raw_tokens': 3})
        self.assertEqual(report['categories']['base_system']['characters'], 5)
        self.assertEqual(report['categories']['fixed_memory']['characters'], 4)
        self.assertEqual(report['categories']['skill_catalog']['characters'], 3)
        self.assertEqual(report['categories']['loaded_skills']['characters'], 2)
        self.assertEqual(report['categories']['tool_results']['characters'], 2)
        self.assertEqual(report['categories']['task_context']['characters'], 3)
        self.assertEqual(report['estimated_tokens'], sum(row['tokens'] for row in report['categories'].values()))
        self.assertEqual(report['raw_estimated_tokens'], sum(row['raw_tokens'] for row in report['categories'].values()))
        self.assertEqual(report['raw_estimated_tokens'] - baseline['raw_estimated_tokens'], 3)
        self.assertEqual(report['estimated_tokens'] - baseline['estimated_tokens'], 5)

    def test_context_settlement_calibrates_with_reasoning_in_the_raw_baseline(self):
        # Omitting reasoning or using the already corrected total distorts the
        # provider ratio observed by the real middleware settlement path.
        from nailong.core.context_runtime import ContextManagerMiddleware

        calibration = UsageCalibration()
        calibration.observe('deepseek-v4-flash', 'example.invalid', 10, 15)
        messages = [HumanMessage(content='next'),
                    AIMessage(content='done', additional_kwargs={'reasoning_content': 'r' * 216_213})]
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings('fixture', 'https://example.invalid', 'deepseek-v4-flash', Path(directory))
            manager = ContextManagerMiddleware(settings, [], '', 'chat', 'one', None, None, {}, calibration)
            request = ModelRequest(model=self.model, messages=messages, tools=[], system_message=None)
            prepared, snapshot = manager._prepare_request(request)
            self.assertEqual(self.model._get_request_payload(prepared.messages)['messages'][1]['reasoning_content'],
                             'r' * 216_213)
            self.assertEqual(snapshot['raw_estimated_tokens'], 54_069)
            self.assertGreater(snapshot['estimated_tokens'], snapshot['raw_estimated_tokens'])
            response = ModelResponse(result=[AIMessage(content='done', usage_metadata={
                'input_tokens': 108_138, 'output_tokens': 1, 'total_tokens': 108_139})])

            manager._settle(response, snapshot)

            self.assertEqual(snapshot['actual_main_input_tokens'], 108_138)
            self.assertEqual(calibration.factor_for('deepseek-v4-flash', 'example.invalid'), 2.0)


if __name__ == '__main__':
    unittest.main()
