import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.sessions import ProjectSessionStore


class RecordingModel(FakeMessagesListChatModel):
    requests: list = []
    caps: list = []

    def bind_tools(self,tools,**kwargs): return self

    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        self.requests.append(list(messages)); self.caps.append(kwargs.get('max_tokens'))
        return super()._generate(messages,stop=stop,run_manager=run_manager,**kwargs)


class RuntimeContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'project'; self.root.mkdir()
        self.data=Path(self.temp.name)/'data'
        self.model=RecordingModel(responses=[AIMessage(content='答复',usage_metadata={'input_tokens':100,'output_tokens':10,'total_tokens':110})])
        self.store=ProjectSessionStore(self.root,base_dir=self.data,api_key='test-secret')
        with patch('agent.ChatDeepSeek',return_value=self.model):
            self.factory=AgentRuntimeFactory(Settings('test-secret','https://example.invalid','private',self.root),session_store=self.store)
        self.addCleanup(self.factory.close)
        self.addAsyncCleanup(self.factory.aclose)

    def configure_window(self,size):
        (self.root/'.nailong').mkdir(exist_ok=True)
        (self.root/'.nailong/settings.json').write_text(json.dumps({'context_windows':{'private':size}}), newline='\n')

    async def ask(self,thread='one',text='读取项目',allowed=None):
        runtime=await self.factory.async_runtime(thread_id=thread,allowed_tools=allowed)
        return await runtime.ainvoke({'messages':[HumanMessage(content=text)]},{'configurable':{'thread_id':thread}},version='v2')

    async def test_actual_filtered_request_report_has_mode_tools_and_usage(self):
        await self.ask(allowed={'read_file'})
        report=AgentService(self.factory).get_context_summary('one')
        self.assertEqual(report.get('source'),'actual_request')
        self.assertEqual(report.get('tools'),['read_file'])
        self.assertEqual(report.get('profile'),'chat')
        self.assertEqual(report['actual_main_input_tokens'],100)
        self.assertEqual(report['estimated_tokens'],sum(r['tokens'] for r in report['categories'].values()))

    async def test_fixed_system_too_large_stops_before_provider_call(self):
        self.configure_window(200)
        from nailong.core.context_runtime import ContextLimitExceeded
        with self.assertRaises(ContextLimitExceeded): await self.ask(allowed=set())
        self.assertEqual(self.model.requests,[])

    async def test_every_tool_loop_call_clears_completed_output_in_checkpoint(self):
        self.configure_window(15000)
        self.model.responses=[AIMessage(content='',tool_calls=[{'name':'read_file','args':{'path':f'{i}.txt'},'id':f'r{i}','type':'tool_call'}]) for i in range(3)]+[AIMessage(content='done')]
        for i in range(3): (self.root/f'{i}.txt').write_text('调查内容'*2800, newline='\n')
        result=await self.ask(allowed={'read_file'})
        self.assertEqual(len(self.model.requests),4)
        final_tools=[m for m in self.model.requests[-1] if isinstance(m,ToolMessage)]
        self.assertLess(sum(len(m.content) for m in final_tools),15000)
        runtime=await self.factory.async_runtime(thread_id='one',allowed_tools={'read_file'})
        state=await runtime.aget_state({'configurable':{'thread_id':'one'}})
        self.assertTrue(any(m.additional_kwargs.get('nailong_tool_reference') for m in state.values['messages']))
        self.assertEqual(result.value['messages'][-1].content,'done')

    async def test_history_tool_is_bound_to_current_thread_and_redacts(self):
        from tools import build_tools
        reference=self.factory.history_archive.save('one',ToolMessage(content='result test-secret',tool_call_id='r',name='run_command'))
        tools=build_tools(profile='chat',thread_id='one',file_session=self.factory._tool_session('one'),history_archive=self.factory.history_archive,memory_store=self.factory.memory_store)
        tool=next(t for t in tools if t.name=='read_history_result')
        data=json.loads(tool.invoke({'reference':reference}))
        self.assertTrue(data['ok']); self.assertNotIn('test-secret',data['content'])
        tools=build_tools(profile='chat',thread_id='two',file_session=self.factory._tool_session('two'),history_archive=self.factory.history_archive)
        data=json.loads(next(t for t in tools if t.name=='read_history_result').invoke({'reference':reference}))
        self.assertFalse(data['ok'])

    async def test_inactive_goal_pin_is_released(self):
        runtime=await self.factory.async_runtime(thread_id='one',allowed_tools=set())
        await runtime.ainvoke({'messages':[HumanMessage(id='old-goal',content='旧目标',additional_kwargs={'nailong_pin':'goal'})]}, {'configurable':{'thread_id':'one'}},version='v2')
        state=await runtime.aget_state({'configurable':{'thread_id':'one'}})
        self.assertFalse(next(m for m in state.values['messages'] if m.id=='old-goal').additional_kwargs.get('nailong_pin'))

    def test_sync_runtime_also_enforces_window(self):
        self.configure_window(200)
        from nailong.core.context_runtime import ContextLimitExceeded
        with self.assertRaises(ContextLimitExceeded):
            self.factory(thread_id='sync',allowed_tools=set()).invoke({'messages':[HumanMessage(content='输入')]},{'configurable':{'thread_id':'sync'}},version='v2')
        self.assertEqual(self.model.requests,[])

    async def test_readonly_child_also_checks_complete_request(self):
        self.configure_window(200)
        from nailong.core.context_runtime import ContextLimitExceeded
        with self.assertRaises(ContextLimitExceeded):
            await self.factory._run_readonly_subagent('调查一个文件')
        self.assertEqual(self.model.requests,[])

    async def test_report_survives_new_factory_without_rebuilding_chat_tools(self):
        await self.ask(allowed={'read_file'})
        self.factory.context_reports.clear()
        report=AgentService(self.factory).get_context_summary('one')
        self.assertEqual(report.get('source'),'actual_request')
        self.assertEqual(report.get('tools'),['read_file'])
        self.assertEqual(report['actual_main_input_tokens'],100)

    async def test_manual_compaction_keeps_summary_before_recent_user_turns(self):
        runtime=await self.factory.async_runtime(thread_id='manual',allowed_tools=set())
        messages=[]
        for i in range(8):
            messages += [HumanMessage(id=f'u{i}',content=f'步骤{i}'),AIMessage(id=f'a{i}',content='资料'*3000)]
        config={'configurable':{'thread_id':'manual'}}
        await runtime.ainvoke({'messages':messages},config,version='v2')
        report=await AgentService(self.factory).compact_context('manual')
        self.assertTrue(report['compacted'])
        state=await runtime.aget_state(config)
        self.assertTrue(state.values['messages'][0].additional_kwargs.get('nailong_compact_summary'))
        self.assertEqual(next(m.id for m in state.values['messages'] if not m.additional_kwargs.get('nailong_compact_summary')),'u4')

    async def test_memory_request_budget_filter_precedes_compaction_decision(self):
        self.configure_window(10000)
        messages=[HumanMessage(content='根据已经读取的记忆回答')]
        for i in range(8):
            messages.extend([
                AIMessage(content='',tool_calls=[{'name':'memory_read','args':{'document':'project/context'},'id':f'm{i}','type':'tool_call'}]),
                ToolMessage(name='memory_read',tool_call_id=f'm{i}',content=json.dumps({'ok':True,'content':'规则'*1000,'offset':0,'total_chars':2000}))])
        await (await self.factory.async_runtime(thread_id='memory',allowed_tools={'memory_read'})).ainvoke(
            {'messages':messages},{'configurable':{'thread_id':'memory'}},version='v2')
        runtime=await self.factory.async_runtime(thread_id='memory',allowed_tools={'memory_read'})
        state=await runtime.aget_state({'configurable':{'thread_id':'memory'}})
        self.assertFalse(any(m.additional_kwargs.get('nailong_tool_cleared') for m in state.values['messages']))
        self.assertLess(self.factory.context_reports['memory']['estimated_tokens'],7500)
