"""The same native call may appear in response, per-call and summary events."""
import unittest
import tempfile
import asyncio
import io
import json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from nailong.core.diagnostics import usage_snapshot
from ui.presentation import SessionMetrics


PRICE = {'input_per_million': 1, 'cache_hit_per_million': .1, 'output_per_million': 2}


def usage(identity, inputs=100, output=10, cache=0, **extra):
    return {'model_call_id': identity, 'input_tokens': inputs, 'output_tokens': output,
            'cache_hit_tokens': cache, 'price_snapshot': PRICE, **extra}


def event(kind, data):
    return {'kind': kind, 'data': data}


class AdaptiveUsageTests(unittest.TestCase):
    def snapshot(self, events):
        return usage_snapshot(SimpleNamespace(read_events=lambda _: events), 'thread')

    def test_duplicate_main_usage_is_settled_once(self):
        rows = [event('usage', usage('main')), event('usage', usage('main'))]
        result = self.snapshot(rows)
        self.assertEqual(result['input_tokens'], 100)
        self.assertEqual(result['total_tokens'], 110)
        self.assertEqual(result['calls'], 1)

    def test_child_summary_and_per_call_records_are_not_added_twice(self):
        first, second = usage('child-1'), usage('child-2', 200)
        rows = [event('model_usage_call', {**first, 'scope': 'subagent'}),
                event('usage', {'scope': 'subagent', 'calls': [first, second],
                                'input_tokens': 300, 'output_tokens': 20}),
                event('usage', {'scope': 'subagent', 'calls': [first, second],
                                'input_tokens': 300, 'output_tokens': 20})]
        result = self.snapshot(rows)
        self.assertEqual(result['input_tokens'], 300)
        self.assertEqual(result['calls'], 2)

    def test_cache_is_input_subset_even_for_malformed_provider_record(self):
        result = self.snapshot([event('usage', usage('main', cache=500))])
        self.assertEqual(result['cache_hit_tokens'], 100)
        self.assertEqual(result['total_tokens'], 110)

    def test_response_record_recovers_accounting_when_turn_stops_before_usage_event(self):
        result = self.snapshot([event('model_response', {'model_call_id': 'main', 'usage': usage('main')})])
        self.assertEqual(result['input_tokens'], 100)
        self.assertEqual(result['calls'], 1)

    def test_live_metrics_ignore_duplicate_call_id(self):
        metrics = SessionMetrics()
        metrics.observe_usage(usage('main'))
        metrics.observe_usage(usage('main'))
        self.assertEqual(metrics.input_tokens, 100)

    def test_projection_separates_per_turn_actuals_cumulative_and_estimates(self):
        from nailong.core.usage_projection import usage_projection
        rows = [event('turn_start', {'turn_id': 'first'}), event('usage', usage('a')),
                event('turn_start', {'turn_id': 'second'}),
                event('context_request', {'request_id': 'request', 'estimated_tokens': 9000,
                      'categories': {'base_system': {'tokens': 8000}, 'history': {'tokens': 1000}}}),
                event('usage', usage('b', 200, 20, 120))]
        result = usage_projection(rows)
        self.assertEqual(result['cumulative']['input_tokens'], 300)
        self.assertEqual(result['last_turn']['actual']['input_tokens'], 200)
        self.assertEqual(result['last_turn']['actual']['cache_hit_tokens'], 120)
        self.assertEqual(result['last_turn']['estimate']['estimated_tokens'], 9000)
        self.assertEqual(result['last_turn']['estimate']['method'], 'estimated')

    def test_missing_usage_remains_unknown_and_is_not_estimated_from_context(self):
        from nailong.core.usage_projection import usage_projection
        result = usage_projection([event('turn_start', {'turn_id': 'one'}),
            event('context_request', {'estimated_tokens': 9000, 'categories': {}}),
            event('usage_missing', {'model_call_id': 'unknown'})])
        self.assertFalse(result['last_turn']['actual']['usage_complete'])
        self.assertIsNone(result['last_turn']['actual']['cost_usd'])
        self.assertEqual(result['last_turn']['actual']['input_tokens'], 0)

    def test_later_known_usage_resolves_same_call_missing_marker(self):
        result = self.snapshot([event('usage_missing', {'model_call_id': 'main'}),
                                event('usage', usage('main'))])
        self.assertTrue(result['usage_complete'])
        self.assertEqual(result['calls'], 1)

    def test_invalid_usage_record_remains_unknown(self):
        result = self.snapshot([event('usage', 'invalid record')])
        self.assertFalse(result['usage_complete'])
        self.assertIsNone(result['cost_usd'])

    def test_partial_input_sample_can_update_context_without_becoming_complete_usage(self):
        rows = [event('turn_start', {'turn_id': 'one'}),
                event('usage', {'model_call_id': 'main', 'input_tokens': 1234})]
        metrics = SessionMetrics.from_events(rows)
        self.assertEqual(metrics.last_input_tokens, 1234)
        self.assertFalse(self.snapshot(rows)['usage_complete'])

    def test_conflicting_same_call_records_are_not_treated_as_precise_bills(self):
        result = self.snapshot([event('usage', usage('main', 100)), event('usage', usage('main', 200))])
        self.assertFalse(result['usage_complete'])
        self.assertIsNone(result['cost_usd'])

    def test_gui_turn_projection_contains_labeled_usage_and_request_estimate(self):
        from nailong.core.branches import project_turn
        rows = [event('turn_start', {'turn_id': 'one'}),
                event('context_request', {'estimated_tokens': 8000, 'categories': {}}),
                event('usage', usage('main'))]
        node = project_turn({'thread_id': 'thread', 'status': 'completed'}, rows)
        self.assertEqual(node['usage_details']['last_turn']['actual']['total_tokens'], 110)
        self.assertEqual(node['usage_details']['last_turn']['estimate']['estimated_tokens'], 8000)

    def test_context_command_shows_actual_and_classified_estimates(self):
        from config import Settings
        from ui.controller import CommandController
        with tempfile.TemporaryDirectory() as directory:
            controller = CommandController(Settings('fixture-key', 'https://fixture.invalid', 'unknown', Path(directory)))
            context = {'estimated_tokens': 8000, 'message_count': 2, 'user_turns': 1,
                       'pinned_messages': 0, 'categories': {'base_system': {'tokens': 7000}, 'history': {'tokens': 1000}}}
            rows = [event('turn_start', {'turn_id': 'one'}), event('usage', usage('main'))]
            service = SimpleNamespace(get_context_summary=lambda _: context,
                compact_threshold_tokens=150000, session_store=SimpleNamespace(read_events=lambda _: rows))
            text = controller.information(controller.resolve('/context'), service, 'thread')
            self.assertIn('本轮实际', text)
            self.assertIn('分类估算', text)
            self.assertIn('7000', text)

    def test_cost_command_uses_deduplicated_recorded_prices(self):
        from config import Settings
        from ui.controller import CommandController
        with tempfile.TemporaryDirectory() as directory:
            controller = CommandController(Settings('fixture-key', 'https://fixture.invalid', 'unknown', Path(directory)))
            rows = [event('usage', usage('main')), event('usage', usage('main'))]
            service = SimpleNamespace(session_store=SimpleNamespace(read_events=lambda _: rows))
            text = controller.information(controller.resolve('/cost'), service, 'thread')
            self.assertIn('输入 100', text)
            self.assertIn('$0.000120', text)


class AdaptiveUsageRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage
        from agent import AgentRuntimeFactory
        from config import Settings
        from nailong.core.sessions import ProjectSessionStore
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)/'project'
        self.root.mkdir()
        home=Path(self.temporary.name)/'home'
        home.mkdir()
        home_patch=patch('pathlib.Path.home',return_value=home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.settings=Settings('fixture-key','https://fixture.invalid','deepseek-flash',self.root)
        self.factory=AgentRuntimeFactory(self.settings,
            session_store=ProjectSessionStore(self.root,base_dir=Path(self.temporary.name)/'data'))
        self.factory.model=FakeMessagesListChatModel(responses=[AIMessage(content='你好。',
            usage_metadata={'input_tokens':100,'output_tokens':10,'total_tokens':110})])

    async def asyncTearDown(self):
        await self.factory.aclose()
        self.factory.close()

    async def test_native_headless_json_includes_deduplicated_actuals_and_estimates(self):
        import headless
        stdout,stderr=io.StringIO(),io.StringIO()
        with patch.object(headless,'create_agent_runtime',return_value=self.factory()):
            code=await headless.run_print(self.settings,'你好',output_format='json',stdout=stdout,stderr=stderr)
        self.assertEqual(code,0,stderr.getvalue())
        result=json.loads(stdout.getvalue())
        self.assertEqual(result['usage']['total_tokens'],110)
        detail=result['usage_details']['last_turn']
        self.assertEqual(detail['actual']['calls'],1)
        self.assertEqual(detail['actual']['total_tokens'],110)
        self.assertEqual(detail['estimate']['method'],'estimated')
        self.assertEqual(detail['estimate']['tools'],[])

    async def test_native_gui_node_detail_exposes_real_turn_and_session_usage(self):
        from agent_service import AgentService
        from nailong.gui.controller import GUIController
        controller=GUIController(self.root,sessions=self.factory.session_store,service=AgentService(self.factory))
        try:
            branch=await controller.command('create',{'name':'计量测试'})
            queued=await controller.command('send',{'branch_id':branch['id'],'message':'你好','request_id':'usage-test'})
            async with asyncio.timeout(5):
                while controller.runs[queued['run_id']]['status'] in {'queued','running','waiting_approval'}:
                    await asyncio.sleep(.01)
            run=controller.runs[queued['run_id']]
            self.assertEqual(run['status'],'completed',run.get('error'))
            detail=await controller.command('node',{'node_id':run['node_id']})
            self.assertEqual(detail['usage_details']['last_turn']['actual']['total_tokens'],110)
            self.assertEqual(detail['session_usage']['total_tokens'],110)
            self.assertEqual(detail['usage_details']['last_turn']['estimate']['tools'],[])
        finally:
            await controller.close()
