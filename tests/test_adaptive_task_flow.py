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
            self.assertTrue(intake.prepare({'objective':'修复报表'},0)['ok'])
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
        import shlex,sys
        self.goals.update(self.goal.id,state='paused',summary='fixture idle',thread_id='stream')
        (self.root/'result.txt').write_text('bad')
        command=shlex.quote(sys.executable)+' -c '+shlex.quote("from pathlib import Path; assert Path('result.txt').read_text()=='ok'")
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
            self.assertTrue(intake.prepare({'objective':'研究项目'},0)['ok']);intake.present()
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
            async with asyncio.timeout(10):
                while controller.runs[queued['run_id']]['status'] in {'queued','running','waiting_approval'}:await asyncio.sleep(.01)
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
        self.assertEqual(self.goals.latest().round,1)
