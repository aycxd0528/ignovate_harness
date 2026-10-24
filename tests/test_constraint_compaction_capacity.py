"""Known windows can retain long constraints while archiving old analysis."""
import json
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from agent import AgentRuntimeFactory
from agent_service import AgentService
from nailong.core.compact import compact_messages
from nailong.core.context_runtime import ContextLimitExceeded
import test_context_runtime as fixture


REQUIREMENT='must keep condition '+'c'*9000+' ORIGINAL_CONSTRAINT_TAIL'
ANALYSIS='prior analysis '*60000
ALLOWED={'read_history_result','read_task_context'}
CONFIG={'configurable':{'thread_id':'one'}}


class ConstraintCompactionCapacityTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixture.RuntimeContextTests.setUp
    configure_window=fixture.RuntimeContextTests.configure_window
    ask=fixture.RuntimeContextTests.ask

    def task(self):
        self.factory.task_store.begin('one','Continue investigation',latest_request=REQUIREMENT)
        return self.factory.task_store.snapshot('one')

    async def history(self):
        runtime=await self.factory.async_runtime(thread_id='one',allowed_tools=ALLOWED)
        await runtime.ainvoke({'messages':[HumanMessage(id='original',content=REQUIREMENT),
            AIMessage(id='old-analysis',content=ANALYSIS),
            HumanMessage(id='current',content='Continue current task')]},CONFIG,version='v2')
        return runtime

    def assert_request(self):
        request=self.model.requests[-1]
        self.assertIn(REQUIREMENT,'\n'.join(str(m.content) for m in request))
        self.assertFalse(any(m.content==ANALYSIS for m in request))
        report=self.factory.context_reports['one']
        self.assertLessEqual(report['estimated_tokens'],report['input_limit'])

    async def test_preflight_retains_full_constraint_and_resumes_from_real_checkpoint(self):
        self.configure_window(128000);original=self.task()
        try:runtime=await self.history()
        except ContextLimitExceeded as error:self.fail('Recoverable old analysis must not block the full constraint: '+str(error))
        self.assert_request()
        state=await runtime.aget_state(CONFIG)
        summary=next(m for m in state.values['messages'] if m.additional_kwargs.get('nailong_compact_summary'))
        self.assertIn(REQUIREMENT,summary.content)
        reference=summary.additional_kwargs['nailong_compact_state']['index_reference']
        index=json.loads(self.factory.history_archive.load('one',reference)['message']['data']['content'])
        self.assertIn(REQUIREMENT,index['constraints'])
        self.assertTrue(any(self.factory.history_archive.load('one',ref)['message']['data']['content']==ANALYSIS
                            for ref in index['evidence']))
        await self.factory.aclose()
        with patch('agent.ChatDeepSeek',return_value=self.model):
            reopened=AgentRuntimeFactory(self.factory.settings,session_store=self.store)
        self.addCleanup(reopened.close);self.addAsyncCleanup(reopened.aclose)
        self.factory=reopened
        await self.ask(text='Continue after reopening',allowed=ALLOWED)
        self.assert_request()
        current=self.factory.task_store.snapshot('one')
        for key in ('task_id','revision','constraints','requirements','request_history'):
            self.assertEqual(current[key],original[key])

    async def test_request_view_compacts_long_constraints_without_replacing_checkpoint_originals(self):
        self.configure_window(128000);self.task()
        path=self.root/'.nailong/settings.json';config=json.loads(path.read_bytes())
        config['context']={'history_input_target_tokens':1};path.write_text(json.dumps(config))
        previous='prior analysis '*4000
        self.model.responses=[AIMessage(content=previous),AIMessage(content='done')]
        await self.ask(text=REQUIREMENT,allowed=ALLOWED)
        await self.ask(text='Continue',allowed=ALLOWED)
        report=self.factory.context_reports['one']
        self.assertTrue(report['history_compaction']['applied'])
        self.assertIn(REQUIREMENT,'\n'.join(str(m.content) for m in self.model.requests[-1]))
        self.assertFalse(any(m.content==previous for m in self.model.requests[-1]))
        runtime=await self.factory.async_runtime(thread_id='one',allowed_tools=ALLOWED)
        state=await runtime.aget_state(CONFIG)
        self.assertTrue(any(m.content==previous for m in state.values['messages']))

    async def test_manual_compaction_uses_known_window_and_keeps_constraint(self):
        self.configure_window(128000);self.task()
        runtime=await self.factory.async_runtime(thread_id='one',allowed_tools=ALLOWED)
        messages=[HumanMessage(id='original',content=REQUIREMENT)]
        for number in range(7):
            messages.extend([AIMessage(content='prior analysis '*2000),HumanMessage(content=f'Continue {number}')])
        await runtime.aupdate_state(CONFIG,{'messages':messages})
        result=await AgentService(self.factory).compact_context('one')
        self.assertTrue(result['compacted'],result)
        self.assertEqual(self.model.requests,[])
        state=await runtime.aget_state(CONFIG)
        summary=next(m for m in state.values['messages'] if m.additional_kwargs.get('nailong_compact_summary'))
        self.assertIn(REQUIREMENT,summary.content)

    async def test_unknown_window_keeps_existing_history_instead_of_expanding_summary(self):
        self.task();runtime=await self.history()
        state=await runtime.aget_state(CONFIG)
        self.assertTrue(any(m.content==REQUIREMENT for m in state.values['messages']))
        self.assertTrue(any(m.content==ANALYSIS for m in state.values['messages']))
        self.assertFalse(any(m.additional_kwargs.get('nailong_compact_summary') for m in state.values['messages']))

    async def test_known_small_window_still_refuses_before_provider(self):
        self.configure_window(6000);original=self.task()
        with self.assertRaises(ContextLimitExceeded):await self.history()
        self.assertEqual(self.model.requests,[])
        self.assertEqual(self.factory.task_store.snapshot('one')['requirements'],original['requirements'])

    async def test_removing_window_after_preflight_cannot_admit_expanded_summary(self):
        from nailong.core.context_runtime import ContextManagerMiddleware
        self.configure_window(128000);self.task();removed=[]
        native=ContextManagerMiddleware._build_request
        def remove_window(manager,request):
            if any(m.additional_kwargs.get('nailong_compact_summary') and REQUIREMENT in str(m.content)
                   for m in request.messages):
                (self.root/'.nailong/settings.json').write_text('{}');removed.append(True)
            return native(manager,request)
        with patch.object(ContextManagerMiddleware,'_build_request',new=remove_window):
            with self.assertRaises(ContextLimitExceeded):await self.history()
        self.assertTrue(removed,'the actual preflight must create the expanded candidate before configuration removal')
        self.assertEqual(self.model.requests,[])

    async def test_removing_window_after_checkpoint_keeps_expanded_history_unadmitted(self):
        self.configure_window(128000);self.task();await self.history()
        self.model.requests.clear();(self.root/'.nailong/settings.json').write_text('{}')
        with self.assertRaises(ContextLimitExceeded):await self.ask(text='Continue with unknown window',allowed=ALLOWED)
        self.assertEqual(self.model.requests,[])

    def test_standalone_compactor_keeps_default_and_explicit_summary_limits(self):
        messages=[HumanMessage(content=REQUIREMENT),AIMessage(content=ANALYSIS),HumanMessage(content='Continue')]
        default=compact_messages(messages,keep_turns=1,level='L2',hard=True)
        self.assertFalse(default.compacted);self.assertEqual(default.messages,messages)
        limited=compact_messages(messages,keep_turns=1,level='L2',hard=True,summary_max_chars=6000)
        self.assertFalse(limited.compacted);self.assertEqual(limited.messages,messages)

    def test_invalid_or_excessive_summary_capacity_is_rejected(self):
        for value in (True,0,-1,1000001,1.5,'20000'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                compact_messages([],summary_max_chars=value)

    def test_absolute_summary_capacity_keeps_oversized_constraints_unchanged(self):
        messages=[HumanMessage(content='must '+'c'*1000001),AIMessage(content=ANALYSIS),HumanMessage(content='Continue')]
        result=compact_messages(messages,keep_turns=1,level='L2',hard=True,summary_max_chars=1000000)
        self.assertFalse(result.compacted);self.assertEqual(result.messages,messages)
