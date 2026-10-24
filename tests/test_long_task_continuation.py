"""A compacted long task must keep its full requirements and resume natively."""
import unittest
import json
import io
import tempfile
from dataclasses import replace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import HumanMessage
from nailong.core.context_runtime import ContextLimitExceeded
import test_context_runtime as context_fixture


LONG_REQUIREMENT='原始有效要求必须继续生效。'*3000+'ORIGINAL_ACTIVE_REQUIREMENT_TAIL'


class LongTaskProjectionTests(unittest.IsolatedAsyncioTestCase):
    setUp=context_fixture.RuntimeContextTests.setUp
    configure_window=context_fixture.RuntimeContextTests.configure_window
    ask=context_fixture.RuntimeContextTests.ask

    def prepare_task(self):
        task=self.factory.task_store.begin('one','继续修复兼容性',latest_request=LONG_REQUIREMENT)
        self.factory.task_store.amend('one',constraints=['KEEP_ORIGINAL_CONSTRAINT'])
        return self.factory.task_store.snapshot('one')

    async def test_known_window_keeps_invisible_full_active_requirement_in_real_request(self):
        self.configure_window(128000);original=self.prepare_task()
        try:await self.ask(text='继续执行原任务',allowed={'read_task_context'})
        except ContextLimitExceeded as error:self.fail('已知窗口足够时完整有效要求不应被固定字符上限阻断：'+str(error))
        request='\n'.join(str(message.content) for message in self.model.requests[-1])
        self.assertIn(LONG_REQUIREMENT,request)
        self.assertIn('KEEP_ORIGINAL_CONSTRAINT',request)
        info=self.factory.context_reports['one']['task_projection']
        self.assertGreater(info['characters'],12000)
        self.assertTrue(info['expanded'])
        current=self.factory.task_store.snapshot('one')
        for key in ('task_id','revision','constraints','request_history','requirements'):
            self.assertEqual(current[key],original[key])
        runtime=await self.factory.async_runtime(thread_id='one',allowed_tools={'read_task_context'})
        state=await runtime.aget_state({'configurable':{'thread_id':'one'}})
        self.assertFalse(any(message.additional_kwargs.get('nailong_task_context') for message in state.values['messages']))

    async def test_request_only_compaction_reports_the_actual_expanded_task_projection(self):
        from langchain_core.messages import AIMessage
        self.configure_window(128000);self.prepare_task()
        path=self.root/'.nailong/settings.json';config=json.loads(path.read_bytes())
        config['context']={'history_input_target_tokens':1};path.write_text(json.dumps(config))
        self.model.responses=[AIMessage(content='prior native analysis '*5000),AIMessage(content='done')]
        allowed={'read_task_context','read_history_result'}
        await self.ask(text=LONG_REQUIREMENT,allowed=allowed)
        await self.ask(text='继续',allowed=allowed)
        projection=next(message for message in self.model.requests[-1]
            if message.additional_kwargs.get('nailong_task_context'))
        report=self.factory.context_reports['one']
        self.assertTrue(report['history_compaction']['applied'])
        self.assertGreater(len(projection.content),12000)
        self.assertIn(LONG_REQUIREMENT,projection.content)
        self.assertEqual(report['task_projection']['characters'],len(projection.content))
        self.assertTrue(report['task_projection']['expanded'])
        requests=[row['data'] for row in self.store.read_events('one') if row['kind']=='context_request']
        self.assertEqual(requests[-1]['task_projection'],report['task_projection'])

    async def assert_original_diagnostic_when_history_candidate_is_rejected(self, unavailable=False):
        from langchain_core.messages import AIMessage
        from contextlib import nullcontext
        self.configure_window(128000);self.prepare_task()
        path=self.root/'.nailong/settings.json';config=json.loads(path.read_bytes())
        config['context']={'history_input_target_tokens':1};path.write_text(json.dumps(config))
        self.model.responses=[AIMessage(content='old'),AIMessage(content='done')]
        allowed={'read_task_context','read_history_result'}
        await self.ask(text=LONG_REQUIREMENT,allowed=allowed)
        native_save=self.factory.history_archive.save
        def refuse_prior_original(thread,message,arguments=None):
            if message.content==LONG_REQUIREMENT:raise OSError('native prior original unavailable')
            return native_save(thread,message,arguments)
        scope=patch.object(self.factory.history_archive,'save',side_effect=refuse_prior_original) if unavailable else nullcontext()
        with scope:await self.ask(text='继续',allowed=allowed)
        actual=self.model.requests[-1]
        projection=next(message for message in actual if message.additional_kwargs.get('nailong_task_context'))
        report=self.factory.context_reports['one']
        self.assertFalse(report['history_compaction']['applied'])
        if unavailable:self.assertEqual(report['history_compaction']['reason'],'archive_unavailable')
        self.assertTrue(any(message.content==LONG_REQUIREMENT for message in actual))
        self.assertEqual(report['task_projection']['characters'],len(projection.content))
        self.assertFalse(report['task_projection']['expanded'])

    async def test_no_gain_history_candidate_keeps_original_projection_diagnostic(self):
        await self.assert_original_diagnostic_when_history_candidate_is_rejected()

    async def test_failed_archive_keeps_original_projection_diagnostic(self):
        await self.assert_original_diagnostic_when_history_candidate_is_rejected(unavailable=True)

    async def test_unknown_window_keeps_strict_projection_and_does_not_call_provider(self):
        original=self.prepare_task()
        with self.assertRaises(ContextLimitExceeded):await self.ask(text='继续',allowed={'read_task_context'})
        self.assertEqual(self.model.requests,[])
        self.assertEqual(self.factory.task_store.snapshot('one')['requirements'],original['requirements'])

    async def test_explicit_projection_limit_is_not_expanded_by_known_window(self):
        import nailong.core.context_runtime as context
        self.configure_window(128000);self.prepare_task();native=context.ContextManagerMiddleware
        def explicit_limit(*args,**kwargs):
            return native(*args,**kwargs,task_context_max_chars=12000)
        with patch.object(context,'ContextManagerMiddleware',side_effect=explicit_limit):
            with self.assertRaises(ContextLimitExceeded):await self.ask(text='继续',allowed={'read_task_context'})
        self.assertEqual(self.model.requests,[])

    async def test_real_request_window_still_refuses_full_requirement_before_provider(self):
        self.configure_window(2000);original=self.prepare_task()
        with self.assertRaises(ContextLimitExceeded):await self.ask(text='继续',allowed={'read_task_context'})
        self.assertEqual(self.model.requests,[])
        limits=[row['data'] for row in self.store.read_events('one') if row['kind']=='context_limit']
        self.assertTrue(limits,'完整有效要求应进入实际输入预算核对，而非被固定投影字符目标提前阻断')
        self.assertGreater(limits[-1]['estimated_tokens'],limits[-1]['input_limit'])
        runtime=await self.factory.async_runtime(thread_id='one',allowed_tools={'read_task_context'})
        state=await runtime.aget_state({'configurable':{'thread_id':'one'}})
        self.assertTrue(any(message.content=='继续' for message in state.values['messages'] if isinstance(message,HumanMessage)))
        self.assertEqual(self.factory.task_store.snapshot('one')['requirements'],original['requirements'])

    async def test_window_removed_before_final_budget_does_not_allow_expanded_request(self):
        import nailong.core.context_runtime as context
        self.configure_window(128000);original=self.prepare_task()
        native=context.ContextManagerMiddleware._policy;calls=0
        def remove_window_before_request_budget(manager,*args,**kwargs):
            nonlocal calls
            calls+=1
            # Real graph: preflight projection/policy, then request projection
            # and its final policy. Change the real config at that last read.
            if calls==4:(self.root/'.nailong/settings.json').write_text(json.dumps({}))
            return native(manager,*args,**kwargs)
        with patch.object(context.ContextManagerMiddleware,'_policy',remove_window_before_request_budget):
            with self.assertRaises(ContextLimitExceeded):await self.ask(text='继续',allowed={'read_task_context'})
        self.assertEqual(self.model.requests,[])
        self.assertEqual(self.factory.task_store.snapshot('one')['requirements'],original['requirements'])

    async def test_expanded_task_is_included_in_native_cumulative_token_admission(self):
        from agent_service import AgentService
        self.configure_window(128000);original=self.prepare_task()
        path=self.root/'.nailong/settings.json';config=json.loads(path.read_bytes())
        config['budgets']={'session_token_limit':20000};path.write_text(json.dumps(config))
        events=[event async for event in AgentService(self.factory).stream_turn('继续',
            {'configurable':{'thread_id':'one'}},allowed_tools={'read_task_context'})]
        self.assertEqual(self.model.requests,[])
        self.assertEqual(events[-1].data.get('output_status'),'budget_stopped')
        budget=self.factory.session_token_budget('one')
        self.assertEqual(budget.spent,0);self.assertEqual(budget.reserved,0)
        self.assertEqual(budget.remaining,20000)
        self.assertGreater(self.factory.context_reports['one']['task_projection']['characters'],12000)
        self.assertEqual(self.factory.task_store.snapshot('one')['requirements'],original['requirements'])

    async def test_redaction_expansion_cannot_bypass_absolute_projection_capacity(self):
        self.configure_window(2000000)
        self.factory.settings=replace(self.factory.settings,api_key='k')
        self.factory.task_store.begin('one','保护完整约束')
        original=self.factory.task_store.amend('one',constraints=['k'*200000])
        with self.assertRaises(ContextLimitExceeded):await self.ask(text='继续',allowed={'read_task_context'})
        self.assertEqual(self.model.requests,[])
        errors=[row['data'] for row in self.store.read_events('one') if row['kind']=='task_context_error']
        self.assertTrue(any('脱敏' in row['reason'] for row in errors))
        self.assertEqual(self.factory.task_store.snapshot('one')['constraints'],original['constraints'])


class LongTaskCompositeTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_compactions_and_restarts_deliver_same_goal_with_complete_requirements(self):
        from long_task_scenario import run_composite_scenario
        with tempfile.TemporaryDirectory() as temporary,redirect_stdout(io.StringIO()):
            result=await run_composite_scenario(Path(temporary).resolve(),cycles=2)
        self.assertEqual(result['rounds'],16)
        self.assertEqual(len(result['compactions']),2)
        self.assertGreater(result['spent'],0)
        self.assertEqual(result['delivery'],'verified')
        self.assertEqual(result['after_external_change'],'unverified')
