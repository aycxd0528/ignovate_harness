"""Natural goal execution must stream and settle like the existing goal driver."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.costs import CostEstimator
from nailong.core.sessions import ProjectSessionStore


def answer(content='进度已保存。', calls=()):
    return AIMessage(content=content,tool_calls=list(calls),usage_metadata={
        'input_tokens':100,'output_tokens':10,'total_tokens':110})


class OfflineModel(FakeMessagesListChatModel):
    def bind_tools(self,tools,**kwargs):
        return self


class GoalStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)/'project'
        self.root.mkdir()
        (self.root/'probe.txt').write_text('native research evidence\n')
        home=Path(self.temporary.name)/'home'
        home.mkdir()
        home_patch=patch('pathlib.Path.home',return_value=home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.factory=AgentRuntimeFactory(Settings('fixture-key','https://fixture.invalid','deepseek-flash',self.root),
            session_store=ProjectSessionStore(self.root,base_dir=Path(self.temporary.name)/'data'))
        self.factory.model=OfflineModel(responses=[answer()])
        self.service=AgentService(self.factory)
        self.goals=self.factory.goal_store
        self.goal=self.goals.create('研究项目',thread_id='stream',max_rounds=2)
        self.estimator=CostEstimator('deepseek-flash',self.root)

    async def asyncTearDown(self):
        await self.factory.aclose()
        self.factory.close()

    def stream(self):
        import ui.flows
        self.assertTrue(callable(getattr(ui.flows,'stream_goal',None)),
                        'shared goal driver needs an incremental async event interface')
        return ui.flows.stream_goal(self.service,self.factory,self.goals,self.estimator,self.goal,
                                     thread_id='stream',approval=lambda *args:'approve_once')

    async def test_first_control_event_arrives_before_any_model_call(self):
        stream=self.stream()
        first=await anext(stream)
        self.assertEqual(first.kind,'goal_control')
        self.assertEqual(self.goals.get(self.goal.id).round,0)
        await stream.aclose()
        self.assertEqual(self.goals.get(self.goal.id).state,'paused')

    async def test_streaming_goal_reuses_native_control_usage_and_state(self):
        self.factory.model.responses=[answer('',[{'name':'update_goal','args':{
            'state':'paused','summary':'等待用户选择'},'id':'pause'}]),answer('等待用户选择。')]
        events=[event async for event in self.stream()]
        saved=self.goals.get(self.goal.id)
        self.assertEqual(saved.state,'paused')
        self.assertEqual(saved.round,1)
        self.assertEqual(len(saved.settled_round_ids),1)
        self.assertEqual(len([event for event in events if event.kind=='usage']),2)
        self.assertTrue(any(event.kind=='goal_progress' for event in events))

    async def test_closing_stream_after_usage_settles_once_without_replaying(self):
        self.factory.model.responses=[answer('',[{'name':'read_file','args':{'path':'probe.txt'},'id':'read'}]),answer()]
        stream=self.stream()
        async for event in stream:
            if event.kind=='usage':
                break
        await stream.aclose()
        await stream.aclose()
        saved=self.goals.get(self.goal.id)
        self.assertEqual(saved.state,'paused')
        self.assertEqual(saved.round,1)
        self.assertEqual(len(saved.settled_round_ids),1)
        self.assertGreater(saved.spent_usd,0)

class NaturalIntakeTests(GoalStreamTests):
    async def test_productive_ordinary_goal_turns_do_not_trigger_idle_pause(self):
        self.goals.update(self.goal.id, state='paused', thread_id='stream')
        goal = self.goals.create('创建项目文件', thread_id='stream', max_rounds=10)
        for index in range(2):
            path = f'output-{index}.txt'
            self.factory.model.i = 0
            self.factory.model.responses = [answer('', [{'name': 'write_file',
                'args': {'path': path, 'content': 'saved'}, 'id': f'write-{index}'}]), answer()]
            events = [event async for event in self.service.stream_turn(
                f'请创建 {path}', {'configurable': {'thread_id': 'stream'}},
                approval_handler=lambda *args: 'approve_once')]
            self.assertTrue(any(event.kind == 'tool_end' and event.data.get('ok') for event in events))
            self.assertEqual((self.root / path).read_text(), 'saved')
        current = self.goals.get(goal.id)
        self.assertEqual(current.round, 2)
        self.assertGreater(current.spent_usd, 0)
        self.assertEqual(current.idle_rounds, 0)
        self.assertEqual(current.state, 'active')

    async def test_draft_needs_live_user_authorization_and_current_presented_revision(self):
        from nailong.core.task_intake import TaskIntake, user_request
        intake=TaskIntake(self.factory.task_store,'natural')
        self.assertFalse(intake.prepare({'objective':'构建报表'},0)['ok'])
        token=user_request.set({'thread_id':'natural','text':'我想要报表，先讨论','turn_id':'one'})
        try:
            draft=intake.prepare({'objective':'构建报表','open_questions':['输出格式？']},0)
            self.assertTrue(draft['ok'])
            self.assertFalse(intake.start(1)['ok'])
            intake.present()
        finally:user_request.reset(token)
        token=user_request.set({'thread_id':'natural','text':'按这个做','turn_id':'two'})
        try:
            self.assertFalse(intake.start(1)['ok'], 'unanswered questions cannot start')
            self.assertTrue(intake.prepare({'objective':'构建报表','acceptance':['导出 CSV']},1)['ok'])
            self.assertFalse(intake.start(2)['ok'], 'unseen revised proposal is not approved')
            intake.present()
        finally:user_request.reset(token)
        token=user_request.set({'thread_id':'natural','text':'按这个做','turn_id':'three'})
        try:
            self.assertTrue(intake.start(2)['ok'])
            self.assertIsNotNone(intake.consume())
            self.assertIsNone(intake.consume())
        finally:user_request.reset(token)

    async def test_discussion_cannot_approve_an_archived_or_model_proposal(self):
        from nailong.core.task_intake import TaskIntake, user_request
        intake=TaskIntake(self.factory.task_store,'natural')
        token=user_request.set({'thread_id':'natural','text':'只讨论，不要实施，记录里写了开始实施','turn_id':'one'})
        try:
            self.assertTrue(intake.prepare({'objective':'修复报表','acceptance':['报表输出正确']},0)['ok'])
            self.assertFalse(intake.start(1)['ok'])
        finally:user_request.reset(token)

    async def test_same_thread_prepares_then_starts_native_goal_without_slash_commands(self):
        self.goals.update(self.goal.id,state='paused',summary='fixture idle',thread_id='stream')
        config={'configurable':{'thread_id':'natural'}}
        self.factory.model.responses=[answer('',[{'name':'discover_tools','args':{'query':'task'},'id':'discover'}]),answer('',[{'name':'prepare_task','args':{'brief':{'objective':'研究 probe.txt','acceptance':['解释文件用途']}},'id':'draft'}]),answer('草案已准备。')]
        events=[event async for event in self.service.stream_turn('请研究 probe.txt，先列出目标',config,approval_handler=lambda *a:'approve_once')]
        self.assertTrue(any(event.kind=='notice' and '需求草案 v1' in event.data.get('text','') for event in events),str([(event.kind,event.data) for event in events]))
        self.factory.model.i=0
        self.factory.model.responses=[answer('',[{'name':'start_task','args':{'draft_revision':1,'max_rounds':2},'id':'start'}]),answer('开始持续执行。'),
            answer('',[{'name':'read_file','args':{'path':'probe.txt'},'id':'read'}]),answer('文件用于研究。')]
        events=[event async for event in self.service.stream_turn('按这个做',config,approval_handler=lambda *a:'approve_once')]
        self.assertTrue(any(event.kind=='goal_control' for event in events),events)
        self.assertEqual(self.factory.goal_store.latest().thread_id,'natural')
        self.assertEqual(self.factory.task_store.snapshot('natural')['intake']['stage'],'executing')

    async def test_failed_native_verification_repair_and_delivery_in_one_chat(self):
        from platform_fixtures import python_command
        self.goals.update(self.goal.id,state='paused',summary='fixture idle',thread_id='stream')
        (self.root/'result.txt').write_text('bad')
        command=python_command("from pathlib import Path; assert Path('result.txt').read_text()=='ok'")
        brief={'objective':'修复 result.txt 使验证通过','scope':['result.txt'],
            'acceptance':[{'description':'结果为 ok','step':'result','paths':['result.txt']}],
            'verification':[{'name':'result','kind':'test','command':command}]}
        self.factory.model.responses=[answer('',[{'name':'prepare_task','args':{'brief':brief},'id':'draft'}]),
            answer('',[{'name':'start_task','args':{'draft_revision':1,'max_rounds':5},'id':'start'}]),answer('开始执行。'),
            answer('先检查当前结果。'),
            answer('',[{'name':'read_file','args':{'path':'result.txt'},'id':'read'}]),
            answer('',[{'name':'write_file','args':{'path':'result.txt','content':'ok'},'id':'write'}]),answer('修复完成，请验证。'),
            answer('',[{'name':'update_goal','args':{'state':'complete','summary':'已验证'},'id':'done'}]),answer('已验证交付。')]
        events=[event async for event in self.service.stream_turn('请修复 result.txt 并持续推进直到验证通过',{'configurable':{'thread_id':'natural'}},approval_handler=lambda *a:'approve_once')]
        self.assertEqual((self.root/'result.txt').read_text(),'ok',str([(e.kind,e.data) for e in events if e.kind in {'notice','tool_end'}]))
        self.assertEqual(self.goals.latest().state,'complete')
        task=self.factory.task_store.snapshot('natural')
        self.assertTrue(any(row['status']=='failed' for row in task['evidence']))
        self.assertTrue(any(row['status']=='passed' for row in task['evidence']))
        self.assertEqual(task['intake']['stage'],'executing')

    async def test_native_child_receipt_contains_actual_file_evidence(self):
        self.factory.task_store.begin('parent','研究 probe.txt')
        self.factory.model.responses=[answer('',[{'name':'read_file','args':{'path':'probe.txt'},'id':'child-read'}]),answer('已读取文件。')]
        result=await self.factory.task_runner.continuations.run(self.factory.task_runner,'parent','读取 probe.txt 后返回')
        self.assertEqual(result['status'],'completed',result)
        self.assertTrue(any(row.get('path')=='probe.txt' and row.get('ok') for row in result['evidence']))

    async def test_closing_natural_pause_stream_clears_live_user_authority(self):
        from nailong.core.task_intake import user_request
        task=self.factory.task_store.begin('stream','研究项目')
        task['intake']={'objective':'研究项目','scope':['.'],'constraints':[],'acceptance':[],
            'open_questions':[],'verification':[],'draft_revision':1,'stage':'executing','presented_turn':'before','pending_start':None}
        self.factory.task_store._write('stream',task)
        stream=self.service.stream_turn('暂停',{'configurable':{'thread_id':'stream'}})
        await anext(stream);await stream.aclose()
        self.assertIsNone(user_request.get(),'closing a UI stream must revoke live authorization')

    async def test_changed_user_requirements_invalidate_previously_presented_brief(self):
        from nailong.core.task_intake import TaskIntake,user_request
        intake=TaskIntake(self.factory.task_store,'natural')
        token=user_request.set({'thread_id':'natural','text':'我想研究项目','turn_id':'draft'})
        try:
            self.assertTrue(intake.prepare({'objective':'研究项目','acceptance':['解释项目用途']},0)['ok']);intake.present()
            self.factory.task_store.amend('natural',constraints=['不得读取私有目录'])
        finally:user_request.reset(token)
        token=user_request.set({'thread_id':'natural','text':'开始实施','turn_id':'start'})
        try:self.assertFalse(intake.start(1)['ok'],'old draft must not override a newer user constraint')
        finally:user_request.reset(token)

    def natural_file_responses(self):
        return [answer('',[{'name':'prepare_task','args':{'brief':{'objective':'创建 output.txt','acceptance':['用户检查文件']}},'id':'brief'}]),
            answer('',[{'name':'start_task','args':{'draft_revision':1,'max_rounds':2},'id':'start'}]),answer('开始执行。'),
            answer('',[{'name':'write_file','args':{'path':'output.txt','content':'native surface'},'id':'write'}]),answer('请检查文件。')]

    async def test_native_gui_runs_natural_goal_through_shared_outer_adapter(self):
        import asyncio
        from nailong.gui.controller import GUIController
        self.goals.update(self.goal.id,state='paused',summary='idle',thread_id='stream')
        self.factory.model.responses=self.natural_file_responses()
        controller=GUIController(self.root,sessions=self.factory.session_store,service=self.service)
        try:
            branch=await controller.command('create',{'name':'自然持续任务'})
            await controller.command('permission',{'branch_id':branch['id'],'mode':'bypassPermissions'})
            queued=await controller.command('send',{'branch_id':branch['id'],'message':'请创建 output.txt 并持续推进','request_id':'natural','permission_mode':'bypassPermissions'})
            # This covers real graph, filesystem and SQLite work, not a UI
            # latency SLA. Join checkpoint/ledger closeout as well as the turn.
            try:
                await asyncio.wait_for(asyncio.shield(controller.worker), 30)
            except TimeoutError:
                self.fail(f"Natural GUI worker did not finish: {controller.runs[queued['run_id']]!r}")
            run=controller.runs[queued['run_id']]
            self.assertEqual(run['status'],'completed',run.get('error'))
            self.assertEqual((self.root/'output.txt').read_text(),'native surface')
            self.assertEqual(self.goals.latest().thread_id,branch['thread_id'])
            self.assertEqual(self.goals.latest().round,1)
        finally:await controller.close()

    async def test_native_headless_runs_natural_goal_without_goal_flag(self):
        import headless,io,json
        self.goals.update(self.goal.id,state='paused',summary='idle',thread_id='stream')
        self.factory.model.responses=self.natural_file_responses()
        out,err=io.StringIO(),io.StringIO()
        with patch.object(headless,'create_agent_runtime',return_value=self.factory()):
            code=await headless.run_print(self.factory.settings,'请创建 output.txt 并持续推进',output_format='json',permission_mode='bypassPermissions',stdout=out,stderr=err)
        self.assertEqual(code,0,err.getvalue());self.assertEqual((self.root/'output.txt').read_text(),'native surface')
        result=json.loads(out.getvalue());self.assertGreater(result['usage']['total_tokens'],110)
        self.assertEqual(result['usage_details']['last_turn']['actual']['total_tokens'],result['usage']['total_tokens'],'one real user request must include hidden goal rounds in the same visible turn')
        self.assertEqual(self.goals.latest().round,1)

    async def test_budget_limited_natural_goal_cannot_be_bypassed_by_rephrasing_work(self):
        self.goals.update(self.goal.id,state='paused',summary='idle',thread_id='stream')
        goal=self.goals.create('修复 output.txt',thread_id='natural',max_cost_usd=1)
        task=self.factory.task_store.begin('natural','修复 output.txt')
        task['intake']={'objective':'修复 output.txt','scope':['.'],'constraints':[],'acceptance':[],
            'open_questions':[],'verification':[],'draft_revision':1,'stage':'executing','presented_turn':'before',
            'task_revision':task['revision'],'pending_start':None,'goal_id':goal.id}
        self.factory.task_store._write('natural',task)
        self.goals.record_round(goal.id,cost_usd=1,files_changed=False,tool_calls=0,round_id='spent')
        self.factory.model.responses=[answer('',[{'name':'write_file','args':{'path':'output.txt','content':'bypassed'},'id':'write'}]),answer('已修改。')]
        events=[e async for e in self.service.stream_turn('请修改 output.txt',{'configurable':{'thread_id':'natural'}},approval_handler=lambda *a:'approve_once')]
        self.assertFalse((self.root/'output.txt').exists(),'rephrased work must keep the exhausted goal budget')
        self.assertFalse(any(e.kind=='usage' for e in events))

    async def test_paused_brief_can_be_revised_without_resetting_goal_ledger(self):
        from nailong.core.task_intake import TaskIntake,user_request
        task=self.factory.task_store.begin('stream','研究项目')
        task['intake']={'objective':'研究项目','scope':['.'],'constraints':[],'acceptance':[],
            'open_questions':[],'verification':[],'draft_revision':1,'stage':'executing','presented_turn':'old',
            'task_revision':task['revision'],'pending_start':None,'goal_id':self.goal.id}
        self.factory.task_store._write('stream',task)
        self.goals.record_round(self.goal.id,cost_usd=.01,files_changed=False,tool_calls=0,round_id='previous')
        [e async for e in self.service.stream_turn('暂停任务',{'configurable':{'thread_id':'stream'}})]
        intake=TaskIntake(self.factory.task_store,'stream')
        token=user_request.set({'thread_id':'stream','text':'修改草案，只研究 probe.txt','turn_id':'revise'})
        try:
            result=intake.prepare({'objective':'只研究 probe.txt','scope':['probe.txt'],'acceptance':['人工检查结论']},1)
            self.assertTrue(result['ok'],result);intake.present()
        finally:user_request.reset(token)
        token=user_request.set({'thread_id':'stream','text':'按这个做','turn_id':'accept'})
        try:
            self.assertTrue(intake.start(2)['ok']);intent=intake.consume()
        finally:user_request.reset(token)
        self.assertEqual(intent['goal_id'],self.goal.id)
        self.assertEqual(self.goals.get(self.goal.id).objective,'只研究 probe.txt')
        self.assertEqual(self.goals.get(self.goal.id).spent_usd,.01)
        self.assertEqual(self.goals.get(self.goal.id).max_cost_usd,self.goal.max_cost_usd)

    async def test_explicit_new_goal_keeps_old_task_and_exhausted_ledger(self):
        self.goals.update(self.goal.id,state='paused',summary='idle',thread_id='stream')
        old=self.goals.create('旧目标',thread_id='natural',max_cost_usd=1)
        task=self.factory.task_store.begin('natural','旧目标')
        task['intake']={'objective':'旧目标','scope':['.'],'constraints':[],'acceptance':[],
            'open_questions':[],'verification':[],'draft_revision':1,'stage':'executing','presented_turn':'old',
            'task_revision':task['revision'],'pending_start':None,'goal_id':old.id}
        self.factory.task_store._write('natural',task)
        self.goals.record_round(old.id,cost_usd=1,files_changed=False,tool_calls=0,round_id='old-cost')
        self.factory.model.responses=self.natural_file_responses()
        [e async for e in self.service.stream_turn('新目标：创建 output.txt',{'configurable':{'thread_id':'natural'}},approval_handler=lambda *a:'approve_once')]
        self.assertEqual((self.root/'output.txt').read_text(),'native surface')
        self.assertNotEqual(self.goals.latest().id,old.id)
        self.assertNotEqual(self.factory.task_store.snapshot('natural')['task_id'],task['task_id'])
        self.assertEqual(self.factory.task_store.work_item(task['task_id'])['task_id'],task['task_id'])
        self.assertEqual(self.goals.get(old.id).spent_usd,1)

    async def test_native_paused_replanning_reaccepts_same_goal_and_implements_revised_brief(self):
        self.goals.update(self.goal.id,state='paused',summary='idle',thread_id='stream')
        goal=self.goals.create('原范围',thread_id='natural',max_rounds=10)
        task=self.factory.task_store.begin('natural','原范围')
        task['intake']={'objective':'原范围','scope':['.'],'constraints':[],'acceptance':[],
            'open_questions':[],'verification':[],'draft_revision':1,'stage':'executing','presented_turn':'old',
            'task_revision':task['revision'],'pending_start':None,'goal_id':goal.id,'implementation_started':True}
        self.factory.task_store._write('natural',task)
        self.goals.record_round(goal.id,cost_usd=.01,files_changed=False,tool_calls=0,round_id='old-stage')
        config={'configurable':{'thread_id':'natural'}}
        [e async for e in self.service.stream_turn('暂停任务',config)]
        self.factory.model.responses=[answer('',[{'name':'prepare_task','args':{'brief':{'objective':'创建 revised.txt','scope':['revised.txt'],'acceptance':['检查文件']},'expected_revision':1},'id':'revise'}]),answer('请核对新草案。')]
        [e async for e in self.service.stream_turn('请修改草案，改为创建 revised.txt',config)]
        self.factory.model.i=0;self.factory.model.responses=[answer('',[{'name':'start_task','args':{'draft_revision':2},'id':'accept'}]),answer('开始新阶段。'),
            answer('',[{'name':'write_file','args':{'path':'revised.txt','content':'same ledger'},'id':'write'}]),answer('请检查结果。')]
        [e async for e in self.service.stream_turn('按这个做',config,approval_handler=lambda *a:'approve_once')]
        self.assertEqual((self.root/'revised.txt').read_text(),'same ledger')
        self.assertEqual(self.goals.latest().id,goal.id)
        self.assertGreater(self.goals.latest().spent_usd,.01)
        self.assertEqual(self.goals.latest().max_rounds,10)

    async def test_native_child_budget_stop_preserves_exact_next_read_position(self):
        from nailong.tools.agents import ReadOnlyTaskRunner
        (self.root/'probe.txt').write_text('visible evidence\n'+'x'*20000)
        self.factory.task_store.begin('parent','完整调查 probe.txt')
        self.factory.model.responses=[answer('',[{'name':'read_file','args':{'path':'probe.txt'},'id':'page-one'}]),answer('部分读取，剩余需接续。')]
        runner=ReadOnlyTaskRunner(self.factory._run_readonly_subagent,token_budget=20000)
        store=self.factory.task_runner.continuations
        first=await store.run(runner,'parent','读取 probe.txt 并说明')
        self.assertEqual(first['status'],'budget_exhausted',first)
        position=first['resume_position']
        self.assertEqual(position['path'],'probe.txt')
        self.assertTrue(position['offset']>1 or position['char_offset']>0)
        self.assertTrue(position['version']);self.assertTrue(first['unresolved'])
        runner.token_budget=100000;runner.begin_turn('parent')
        self.factory.model.i=0;self.factory.model.responses=[answer('',[{'name':'read_file','args':{'path':position['path'],'offset':position['offset'],'char_offset':position['char_offset']},'id':'page-two'}]),answer('补读完成。')]
        final=await store.run(runner,'parent','按保存位置补读',first['resume_reference'])
        self.assertEqual(final['status'],'completed',final)
        self.assertEqual(final['evidence'][0]['offset'],position['offset'])

    async def test_natural_start_requires_an_observable_acceptance_condition(self):
        from nailong.core.task_intake import TaskIntake,user_request
        intake=TaskIntake(self.factory.task_store,'natural')
        token=user_request.set({'thread_id':'natural','text':'请优化性能','turn_id':'unclear'})
        try:
            self.assertTrue(intake.prepare({'objective':'优化性能'},0)['ok'])
            self.assertFalse(intake.start(1)['ok'],'implementation must wait for an observable outcome')
        finally:user_request.reset(token)

    async def test_new_goal_does_not_reload_previous_goal_discoveries(self):
        from test_adaptive_capabilities import RequestProbe
        from ui.flows import stream_goal
        model=RequestProbe(responses=[answer('',[{'name':'discover_tools','args':{'query':'session','limit':10},'id':'session'}]),
            answer('',[{'name':'update_goal','args':{'state':'paused','summary':'阶段完成'},'id':'pause'}]),answer('已暂停。')])
        self.factory.model=model
        [e async for e in stream_goal(self.service,self.factory,self.goals,self.estimator,self.goal,thread_id='stream',approval=lambda *a:'approve_once')]
        self.assertIn('coordination_status',model._bindings[1])
        second=self.goals.create('新的独立调查',thread_id='stream')
        model.i=0;model.responses=[answer('',[{'name':'update_goal','args':{'state':'paused','summary':'等待'},'id':'second-pause'}]),answer('等待。')]
        start=len(model._bindings)
        [e async for e in stream_goal(self.service,self.factory,self.goals,self.estimator,second,thread_id='stream',approval=lambda *a:'approve_once')]
        self.assertNotIn('coordination_status',model._bindings[start])

class LiveIntakeContractTests(unittest.TestCase):
    def test_registered_brief_exposes_automatic_acceptance_and_verification_contract(self):
        from nailong.core.task_intake import TaskIntake
        from nailong.tools.schemas import parameter_model
        model=parameter_model('prepare_task',TaskIntake(None,'wire').prepare)
        schema=model.model_json_schema()
        brief=schema['properties']['brief']
        self.assertIn('$ref',brief,'provider must see nested brief fields instead of an opaque object')
        definitions=schema['$defs']
        self.assertIn('acceptance',definitions[brief['$ref'].split('/')[-1]]['properties'])
        verification=next(row for row in definitions.values() if {'name','kind','command'}<=set(row.get('properties',{})))
        self.assertEqual(set(verification['properties']['kind']['enum']),{'test','build','run'})
        value=model.model_validate({'brief':{'objective':'转大写','acceptance':[{'description':'两项测试通过','step':'unit','paths':['upper.py','test_upper.py']}],
            'verification':[{'name':'unit','kind':'test','command':'python -m unittest test_upper','timeout_seconds':30}]}}).model_dump()
        self.assertEqual(value['brief']['acceptance'][0]['step'],'unit')
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            model.model_validate({'brief':{'objective':'目标','unknown':'must not silently disappear'}})

    def test_full_task_prompt_preserves_natural_start_protocol(self):
        # Use the normal factory's public prompt assembly fixture surface.
        from nailong.core.capabilities import CapabilityCatalog
        from langchain_core.messages import HumanMessage
        from types import SimpleNamespace
        full='full core policy'
        catalog=CapabilityCatalog(prompt_builder=lambda names:{'base_system':full},task_provider=lambda:{'task_id':'one'})
        catalog.tools={name:SimpleNamespace(name=name) for name in ('prepare_task','start_task','write_file','discover_tools','search_skills')}
        view=catalog.view({'messages':[HumanMessage(content='请实施并持续推进',id='current')]},'')
        prompt=view['parts']['base_system']
        self.assertIn('prepare_task',prompt)
        self.assertIn('start_task',prompt)
        self.assertIn('本轮结束',prompt)
