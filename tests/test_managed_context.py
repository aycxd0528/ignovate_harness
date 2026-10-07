"""Offline contract and real-graph verification for current task projections."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from agent import AgentRuntimeFactory
from config import Settings
from nailong.core.budgets import GoalCostBudget, SharedTokenBudget, active_cost_budget, active_token_budget, _input_bound
from nailong.core.costs import CostEstimator
from nailong.core.context import UsageCalibration, context_budget, request_report
from nailong.core.context_runtime import ContextLimitExceeded, ContextManagerMiddleware
from nailong.core.memory import MemoryStore, MemorySnapshot, load_project_memory
from nailong.core.memory_context import MemoryReadContext
from nailong.core.memory_selection import select_core_memory
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_context import TaskContextError, TaskContextTooLargeError, render_task_context, validate_task_snapshot


def snapshot(root, thread='one'):
    return {'schema_version':1,'task_id':'task-one','thread_id':thread,'project_root':str(root),
        'objective':'修复认证','scope':['src'],'constraints':['禁止更改数据库结构'],
        'revision':1,'latest_request':'修复认证','request_history':['修复认证'],'profile':'chat',
        'lifecycle':'active','phase':'implement','steps':[{'id':'fix','title':'修复认证入口','state':'doing','dependencies':[]}],
        'acceptance':[{'id':'login','description':'登录成功且旧账户仍可用','kind':'test','required':True,
                       'status':'pending','evidence_ids':[],
                       'verification_binding':{'step':'auth','paths':['src/auth.py'],'source':'user'}}],
        'changed_paths':['src/auth.py'],'pending_verification':['尚未运行认证回归'],
        'evidence':[],'progress':'已定位入口','blockers':[],'input_fingerprint':''}


class OfflineModel(FakeMessagesListChatModel):
    requests: list = []
    caps: list = []
    max_tokens: int | None = None
    def bind_tools(self, tools, **kwargs): return self
    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        self.requests.append(list(messages)); self.caps.append(kwargs.get('max_tokens'))
        return super()._generate(messages,stop=stop,run_manager=run_manager,**kwargs)


class TaskRenderingTests(unittest.TestCase):
    def setUp(self): self.task=snapshot(Path('/project'))

    def test_complete_requirements_and_bindings_are_preserved_without_mutation(self):
        self.task['request_history'] += ['保留第二步的历史要求','第四步增加日志']
        self.task['latest_request']='第四步增加日志'
        original=copy.deepcopy(self.task)
        text=render_task_context(self.task)
        for value in ('保留第二步的历史要求','禁止更改数据库结构','修复认证入口','尚未运行认证回归','src/auth.py','verification_binding'):
            self.assertIn(value,text)
        self.assertEqual(self.task,original)
        self.assertEqual(text,render_task_context(self.task))

    def test_unrelated_counters_and_timestamps_do_not_change_projection(self):
        expected=render_task_context(self.task)
        self.task.update(updated_at='2099-01-01',progress_repeats=99,progress_observations=[{'request_id':'random'}])
        self.assertEqual(render_task_context(self.task),expected)

    def test_none_and_legacy_history_are_explicit(self):
        self.assertEqual(render_task_context(None),'')
        self.task.pop('request_history')
        self.assertIn('不能确认中间要求',render_task_context(self.task))

    def test_protected_overflow_fails_instead_of_dropping_requirements(self):
        self.task['constraints']=['必须保留'+ '约束'*9000]
        with self.assertRaises(TaskContextTooLargeError): render_task_context(self.task)
        self.assertEqual(len(self.task['constraints'][0]),18004)

    def test_model_unknown_stale_and_missing_evidence_remain_visible(self):
        self.task['acceptance'][0].update(status='passed',evidence_ids=['missing','model'])
        self.task['revision']=2; self.task['input_fingerprint']='current'
        self.task['evidence']=[{'id':'model','kind':'test','source':'model','status':'passed','task_revision':1,
            'paths':['src/auth.py'],'input_fingerprint':'old','coverage':'unknown','summary':'我认为通过了','artifact_ref':'verification:old'}]
        text=render_task_context(self.task)
        for value in ('仅为 model 声明','覆盖未知','证据版本不匹配','旧证据','missing_evidence_ids','verification:old'):
            self.assertIn(value,text)

    def test_binding_rejects_model_source_empty_step_and_escape(self):
        for bad in ({'step':'auth','paths':['src'],'source':'model'},
                    {'step':' ','paths':['src'],'source':'user'},
                    {'step':'auth','paths':['../secret'],'source':'user'}):
            with self.subTest(binding=bad):
                self.task['acceptance'][0]['verification_binding']=bad
                with self.assertRaises(TaskContextError): validate_task_snapshot(self.task)


class ContextFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.task=snapshot(self.root)
        self.settings=Settings('test-placeholder','https://example.invalid','private',self.root)
        self.model=OfflineModel(responses=[AIMessage(content='done')])
        self.manager=ContextManagerMiddleware(self.settings,[],'BASE','chat','one',None,None,{},UsageCalibration(),
            task_snapshot_provider=lambda identity:copy.deepcopy(self.task))

    def request(self, messages=None, system='BASE'):
        return ModelRequest(model=self.model,messages=messages or [HumanMessage(content='开始')],tools=[],
            system_message=SystemMessage(content=system) if system is not None else None)

    def window(self,size):
        (self.root/'.nailong').mkdir(exist_ok=True)
        (self.root/'.nailong/settings.json').write_text(json.dumps({'context_windows':{'private':size}}))

class MiddlewareTests(ContextFixture):
    def test_duplicate_task_wrapping_has_one_tail_and_exclusive_budget(self):
        original=self.request(); prepared,first=self.manager._prepare_request(original)
        again,second=self.manager._prepare_request(prepared)
        projections=[m for m in again.messages if m.additional_kwargs.get('nailong_task_context')]
        self.assertEqual(len(projections),1)
        self.assertEqual(again.messages[-1],projections[0])
        self.assertNotIn('nailong_task_context',original.messages[0].additional_kwargs)
        self.assertGreater(second['categories']['task_context']['tokens'],0)
        self.assertEqual(first['estimated_tokens'],second['estimated_tokens'])
        self.assertEqual(second['estimated_tokens'],sum(row['tokens'] for row in second['categories'].values()))

    def test_same_revision_progress_is_refreshed_and_old_revision_is_rejected(self):
        self.task['revision']=2
        self.manager._prepare_request(self.request())
        self.task['phase']='verify'; self.task['steps'][0]['state']='done'
        request,report=self.manager._prepare_request(self.request())
        self.assertEqual(report['task_projection']['phase'],'verify')
        self.assertIn('"phase":"verify"',request.messages[-1].content)
        self.task['revision']=1
        with self.assertRaises(ContextLimitExceeded): self.manager._prepare_request(self.request())

    def test_same_revision_new_requirements_fail_but_continuation_is_allowed(self):
        self.manager._prepare_request(self.request())
        self.task['latest_request']='继续'
        self.manager._prepare_request(self.request())
        self.task['latest_request']='新增迁移要求'
        with self.assertRaises(ContextLimitExceeded): self.manager._prepare_request(self.request())

    def test_new_revision_cannot_erase_middle_requirements(self):
        self.task['request_history'] += ['中间要求必须保留','最新要求']
        self.task['latest_request']='最新要求'
        self.manager._prepare_request(self.request())
        self.task['revision']+=1
        self.task['request_history']=['修复认证','新的要求']
        self.task['latest_request']='新的要求'
        with self.assertRaises(ContextLimitExceeded): self.manager._prepare_request(self.request())

    def test_same_revision_cannot_change_acceptance_or_user_verification_binding(self):
        # Acceptance definitions are requirements, unlike their execution status.
        self.manager._prepare_request(self.request())
        original=copy.deepcopy(self.task['acceptance'])
        for field,value in [('description','允许删除旧账户'),('required',False),
                            ('verification_binding',{'step':'different','paths':['src/other.py'],'source':'user'})]:
            with self.subTest(field=field):
                self.task['acceptance']=copy.deepcopy(original)
                self.task['acceptance'][0][field]=value
                with self.assertRaises(ContextLimitExceeded): self.manager._prepare_request(self.request())

    def test_new_revision_acceptance_and_same_revision_execution_status_are_refreshed(self):
        self.manager._prepare_request(self.request())
        self.task['revision']+=1
        self.task['acceptance'][0]['description']='保留账户和访问日志'
        prepared,_=self.manager._prepare_request(self.request())
        self.assertIn('保留账户和访问日志',prepared.messages[-1].content)
        self.task['acceptance'][0]['status']='failed'
        prepared,_=self.manager._prepare_request(self.request())
        self.assertIn('"status":"failed"',prepared.messages[-1].content)

    def test_identity_is_bound_to_thread_and_project(self):
        for key,value in [('thread_id','other'),('project_root','/unrelated')]:
            with self.subTest(key=key):
                task=copy.deepcopy(self.task); task[key]=value
                self.manager.task_snapshot_provider=lambda identity:task
                with self.assertRaises(ContextLimitExceeded): self.manager._prepare_request(self.request())

    def test_pending_parallel_exchange_is_not_split_or_mutated(self):
        messages=[HumanMessage(content='read'),AIMessage(content='',tool_calls=[
            {'name':'read_file','id':'a','args':{}},{'name':'read_file','id':'b','args':{}}]),
            ToolMessage(content='partial',tool_call_id='a',name='read_file')]
        original=copy.deepcopy(messages)
        with self.assertRaises(ContextLimitExceeded): self.manager._prepare_request(self.request(messages))
        self.assertEqual(messages,original)

    def test_task_input_cannot_be_dropped_to_fit_window(self):
        self.window(500)
        original=self.request()
        with self.assertRaises(ContextLimitExceeded): self.manager._prepare_request(original)
        self.assertEqual(len(original.messages),1)

    def test_actual_absent_system_does_not_count_compiled_prompt(self):
        self.manager.task_snapshot_provider=None
        self.manager.system_prompt='compiled'*1000
        _,report=self.manager._prepare_request(self.request(system=None))
        self.assertEqual(report['categories']['base_system']['characters'],0)

    def test_compaction_saves_history_but_not_task_projection(self):
        self.window(12000)
        messages=[]
        for i in range(8): messages += [HumanMessage(id=f'u{i}',content=f'旧消息{i}'),AIMessage(id=f'a{i}',content='资料'*3000)]
        update=self.manager.before_model({'messages':messages},None)
        self.assertTrue(update)
        self.assertFalse(any(m.additional_kwargs.get('nailong_task_context') for m in update['messages']))
        retained=[m for m in update['messages'] if m.type!='remove']
        prepared,report=self.manager._prepare_request(self.request(retained))
        self.assertIn('禁止更改数据库结构',prepared.messages[-1].content)
        self.assertLessEqual(report['estimated_tokens'],report['input_limit'])


class ControlledMemory:
    def __init__(self,initial=''):
        self.initial_context=initial; self.context=initial; self.refreshes=0
        self.report={'max_tokens':4000,'context_tokens':0,'scopes':{'project':{'validity':'observed'}}}
        self.filtered=[]
    def refresh_for_request(self):
        self.refreshes+=1
        return {'initial_context':self.initial_context,'context':self.context,'report':self.report}
    def filter_messages(self,messages):
        self.filtered.append(self.context)
        return list(messages)


class MemoryRefreshTests(ContextFixture):
    # Reuse request fixtures, not the middleware contract tests themselves.
    def setup_memory(self,initial='',parts=True):
        memory=ControlledMemory(initial)
        self.manager=ContextManagerMiddleware(self.settings,[],'BASE'+initial+'CAT','chat','one',None,None,{},UsageCalibration(),
            parts={'base_system':'BASE','fixed_memory':initial,'skill_catalog':'CAT'} if parts else None,
            memory_request_context=memory)
        return memory

    def test_empty_initial_memory_repeated_wrap_and_empty_roundtrip(self):
        memory=self.setup_memory()
        memory.context='[MEMORY] new'
        request=self.request(system='BASECAT')
        prepared,_=self.manager._prepare_request(request)
        prepared,_=self.manager._prepare_request(prepared)
        self.assertEqual(prepared.system_message.content,'BASE[MEMORY] newCAT')
        memory.context=''
        prepared,_=self.manager._prepare_request(prepared)
        self.assertEqual(prepared.system_message.content,'BASECAT')
        memory.context='[OTHER] new'
        prepared,_=self.manager._prepare_request(prepared)
        self.assertEqual(prepared.system_message.content,'BASE[OTHER] newCAT')
        self.assertEqual(request.system_message.content,'BASECAT')

    def test_preflight_and_actual_refresh_report_are_frozen(self):
        memory=self.setup_memory('[OLD]')
        memory.context='[FIRST]'
        self.manager.before_model({'messages':[HumanMessage(content='read')]},None)
        self.assertEqual(memory.refreshes,1)
        memory.context='[LATEST]'
        request,report=self.manager._prepare_request(self.request(system='BASE[OLD]CAT'))
        self.assertEqual(memory.refreshes,2)
        self.assertEqual(memory.filtered[-1],'[LATEST]')
        self.assertEqual(request.system_message.content,'BASE[LATEST]CAT')
        self.assertEqual(self.manager.parts['fixed_memory'],'[LATEST]')
        memory.report['scopes']['project']['validity']='stale'
        self.assertEqual(report['memory_budget']['scopes']['project']['validity'],'observed')

    def test_changed_system_marker_stops_instead_of_appending(self):
        memory=self.setup_memory(); memory.context='NEW'
        prepared,_=self.manager._prepare_request(self.request(system='BASECAT'))
        tampered=prepared.override(system_message=prepared.system_message.model_copy(update={'content':'changed'}))
        with self.assertRaises(ContextLimitExceeded): self.manager._prepare_request(tampered)

    def test_repeated_initial_fragment_uses_exact_compiled_slot(self):
        memory=self.setup_memory('BASE'); memory.context='NEW'
        prepared,_=self.manager._prepare_request(self.request(system='BASEBASECAT'))
        self.assertEqual(prepared.system_message.content,'BASENEWCAT')

    def test_refreshed_system_memory_is_in_complete_window_guard(self):
        memory=self.setup_memory(); memory.context='记忆'*2000
        self.window(1000)
        with self.assertRaises(ContextLimitExceeded): self.manager._prepare_request(self.request(system='BASECAT'))


class ManagedGraphTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()/'project'; self.root.mkdir()
        self.store=ProjectSessionStore(self.root,base_dir=Path(self.temp.name)/'data')
        self.settings=Settings('fake-secret','https://example.invalid','private',self.root)
        self.model=OfflineModel(responses=[AIMessage(content='done',usage_metadata={'input_tokens':100,'output_tokens':5,'total_tokens':105})])
        self.user=Path(self.temp.name)/'user/.nailong/context.md'
        original_loader=load_project_memory
        with patch('agent.ChatDeepSeek',return_value=self.model), patch('agent.load_project_memory',side_effect=lambda root,**kwargs:original_loader(root,**{**kwargs,'user_file':self.user})):
            self.factory=AgentRuntimeFactory(self.settings,session_store=self.store)
        self.addCleanup(self.factory.close); self.addAsyncCleanup(self.factory.aclose)
        patcher=patch('agent.load_project_memory',side_effect=lambda root,**kwargs:original_loader(root,**{**kwargs,'user_file':self.user}))
        patcher.start(); self.addCleanup(patcher.stop)

    def test_sync_factory_projects_current_task_without_checkpoint_injection(self):
        self.factory.task_store.begin('sync','修复认证')
        self.factory.task_store.amend('sync',constraints=['保留已有账户'])
        runtime=self.factory(thread_id='sync',allowed_tools=set())
        config={'configurable':{'thread_id':'sync'}}
        runtime.invoke({'messages':[HumanMessage(content='继续')]},config,version='v2')
        self.assertIn('保留已有账户',self.model.requests[-1][-1].content)
        self.assertTrue(self.model.requests[-1][-1].additional_kwargs.get('nailong_task_context'))
        state=runtime.get_state(config)
        self.assertFalse(any(m.additional_kwargs.get('nailong_task_context') for m in state.values['messages']))

    async def test_real_factory_request_has_task_tail_without_checkpoint_projection(self):
        self.factory.task_store.begin('one','修复认证')
        self.factory.task_store.amend('one',constraints=['禁止更改数据库结构'])
        runtime=await self.factory.async_runtime(thread_id='one',allowed_tools=set())
        config={'configurable':{'thread_id':'one'}}
        await runtime.ainvoke({'messages':[HumanMessage(content='继续')]},config,version='v2')
        request=self.model.requests[-1]
        self.assertTrue(request[-1].additional_kwargs.get('nailong_task_context'))
        self.assertIn('禁止更改数据库结构',request[-1].content)
        state=await runtime.aget_state(config)
        self.assertFalse(any(m.additional_kwargs.get('nailong_task_context') for m in state.values['messages']))
        self.assertGreater(self.factory.context_reports['one']['categories']['task_context']['tokens'],0)

    async def test_real_factory_no_task_stays_ordinary_chat(self):
        runtime=await self.factory.async_runtime(thread_id='plain',allowed_tools=set())
        await runtime.ainvoke({'messages':[HumanMessage(content='你好')]},{'configurable':{'thread_id':'plain'}},version='v2')
        self.assertFalse(any(m.additional_kwargs.get('nailong_task_context') for m in self.model.requests[-1]))

    async def test_accounting_reservation_includes_full_task_input(self):
        self.factory.task_store.begin('one','修复认证并保留接口：'+'约束'*1200)
        runtime=await self.factory.async_runtime(thread_id='one',allowed_tools=set())
        budget=SharedTokenBudget(200000)
        seen=[]; reserve=budget.reserve
        async def inspect(request):
            seen.append(request)
            return await reserve(request)
        with patch.object(budget,'reserve',side_effect=inspect):
            token=active_token_budget.set(budget)
            try:
                await runtime.ainvoke({'messages':[HumanMessage(content='继续')]},{'configurable':{'thread_id':'one'}},version='v2')
            finally: active_token_budget.reset(token)
        self.assertTrue(seen)
        self.assertTrue(seen[0].messages[-1].additional_kwargs.get('nailong_task_context'))
        ordinary=seen[0].override(messages=[m for m in seen[0].messages if not m.additional_kwargs.get('nailong_task_context')])
        self.assertGreater(_input_bound(seen[0])-_input_bound(ordinary),6000)

    async def test_goal_cost_reserves_task_input_and_settles_known_usage(self):
        # Catch a middleware reorder that reserves before task projection.
        (self.root/'.nailong').mkdir(exist_ok=True)
        (self.root/'.nailong/settings.json').write_text(json.dumps({'pricing':{'private':{
            'input_per_million':1,'cache_hit_per_million':1,'output_per_million':1}}}))
        objective='修复认证并保留接口：'+'约束'*1200
        self.factory.task_store.begin('cost',objective)
        runtime=await self.factory.async_runtime(thread_id='cost',allowed_tools=set())
        budget=GoalCostBudget(CostEstimator('private',self.root),1.0)
        seen=[]; holds=[]; reserve=budget.reserve
        async def inspect(request):
            seen.append(request)
            hold=await reserve(request)
            holds.append(hold)
            return hold
        with patch.object(budget,'reserve',side_effect=inspect):
            token=active_cost_budget.set(budget)
            try:
                await runtime.ainvoke({'messages':[HumanMessage(content='继续')]},
                    {'configurable':{'thread_id':'cost'}},version='v2')
            finally: active_cost_budget.reset(token)
        self.assertIn(objective,seen[0].messages[-1].content)
        self.assertTrue(seen[0].messages[-1].additional_kwargs.get('nailong_task_context'))
        self.assertGreater(holds[0][0],0.0072)  # 1,200 × 2 Chinese characters × 3 UTF-8 bytes alone.
        self.assertEqual(self.model.caps[-1],holds[0][1])
        self.assertAlmostEqual(budget.spent,0.000105)  # 100 input + 5 output, at $1/M each.
        self.assertEqual(budget.reserved,0)
        self.assertTrue(budget.usage_complete)

    async def test_actual_memory_refresh_after_tool_step_updates_validity_without_history_injection(self):
        (self.root/'.nailong').mkdir()
        path=self.root/'.nailong/context.md'; path.write_text('# 核心约定\n使用已有认证模块。')
        self.model.responses=[AIMessage(content='',tool_calls=[{'name':'read_file','id':'read','args':{'path':'example.txt'},'type':'tool_call'}]),AIMessage(content='done')]
        (self.root/'example.txt').write_text('original')
        original_generate=self.model._generate
        def generate(messages,stop=None,run_manager=None,**kwargs):
            response=original_generate(messages,stop=stop,run_manager=run_manager,**kwargs)
            if len(self.model.requests)==1: path.write_text('# 核心约定\n记忆版本已更新。')
            return response
        runtime=await self.factory.async_runtime(thread_id='one',allowed_tools={'read_file'})
        with patch.object(OfflineModel,'_generate',side_effect=generate):
            await runtime.ainvoke({'messages':[HumanMessage(content='读取 example.txt')]},{'configurable':{'thread_id':'one'}},version='v2')
        self.assertEqual(len(self.model.requests),2)
        first=self.model.requests[0][0].content; second=self.model.requests[1][0].content
        self.assertNotEqual(first,second)
        self.assertIn('validity=stale',second)
        self.assertEqual(second.count('使用已有认证模块。'),1)
        state=await runtime.aget_state({'configurable':{'thread_id':'one'}})
        self.assertFalse(any(m.type=='system' for m in state.values['messages']))
