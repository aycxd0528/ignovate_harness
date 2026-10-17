import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from langchain_core.messages import AIMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.sessions import ProjectSessionStore

class OfflineModel(FakeMessagesListChatModel):
    def bind_tools(self,tools,**kwargs):return self

def answer(text='保存进度。',calls=()):return AIMessage(content=text,tool_calls=list(calls),usage_metadata={'input_tokens':100,'output_tokens':10,'total_tokens':110})

class CoordinationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)/'project';root.mkdir();self.root=root
        home=Path(self.tmp.name)/'home';home.mkdir();p=patch('pathlib.Path.home',return_value=home);p.start();self.addCleanup(p.stop)
        self.factory=AgentRuntimeFactory(Settings('fixture-key','https://fixture.invalid','deepseek-flash',root),session_store=ProjectSessionStore(root,base_dir=Path(self.tmp.name)/'data'))
        self.factory.model=OfflineModel(responses=[answer()]);self.service=AgentService(self.factory)
        self.factory.task_store.begin('target','修复 target.txt')
        [e async for e in self.service.stream_turn('先分析已有上下文',{'configurable':{'thread_id':'target'}})]
        self.goal=self.factory.goal_store.create('协调已有会话 target 完成文件修复',thread_id='owner',max_rounds=10)

    async def asyncTearDown(self):
        await self.factory.aclose();self.factory.close()

    def coordinator(self):
        from nailong.core.session_coordination import SessionCoordination
        return SessionCoordination(self.factory,'owner')

    async def test_dispatch_continues_existing_checkpoint_preserves_identity_and_settles_goal(self):
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        acquired=c.acquire('target',task['revision'],['.'])
        self.assertTrue(acquired['ok'],acquired)
        row=c.dispatch('request-1','target','修复 target.txt',acquired['generation'],[])
        self.assertTrue(row['ok'],row)
        self.assertEqual(c.dispatch('request-1','target','修复 target.txt',acquired['generation'],[])['request_id'],'request-1')
        self.factory.model.i=0;self.factory.model.responses=[answer('',[{'name':'write_file','args':{'path':'target.txt','content':'native coordinated output'},'id':'write'}]),answer('文件已创建。')]
        events=[e async for e in c.drain(self.service,approval=lambda *a:'approve_once')]
        self.assertTrue(events);self.assertEqual((self.root/'target.txt').read_text(),'native coordinated output')
        saved=self.factory.task_store.snapshot('target')
        self.assertEqual((saved['task_id'],saved['revision']),(task['task_id'],task['revision']))
        self.assertEqual(c.status('request-1')['dispatches'][0]['state'],'completed')
        self.assertEqual(self.factory.goal_store.get(self.goal.id).thread_id,'owner')
        self.assertGreater(self.factory.goal_store.get(self.goal.id).spent_usd,0)
        self.assertEqual([e async for e in c.drain(self.service)],[])

    async def test_dependencies_duplicate_mismatch_foreign_target_and_scope(self):
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',task['revision'],['target.txt']);self.assertTrue(lease['ok'])
        self.assertFalse(c.acquire('../foreign',1,['.'])['ok'])
        self.assertFalse(c.dispatch('self','target','work',lease['generation'],['self'])['ok'])
        self.assertTrue(c.dispatch('first','target','inspect',lease['generation'],[])['ok'])
        self.assertFalse(c.dispatch('first','target','different',lease['generation'],[])['ok'])
        self.assertFalse(c.dispatch('second','target','work',lease['generation'],['missing'])['ok'])

    async def test_actual_registered_tools_submit_and_outer_chat_drains_same_queue(self):
        target=self.factory.task_store.snapshot('target')
        self.factory.model.i=0
        self.factory.model.responses=[answer('',[{'name':'discover_tools','args':{'query':'session','limit':10},'id':'discover'}]),
            answer('',[{'name':'take_session','args':{'thread_id':'target','expected_revision':target['revision'],'scope':['.']},'id':'take'}]),
            answer('',[{'name':'dispatch_session','args':{'request_id':'native','thread_id':'target','stage':'创建 target.txt','generation':1,'dependencies':[]},'id':'dispatch'}]),answer('已提交协调。'),
            answer('',[{'name':'write_file','args':{'path':'target.txt','content':'native tool path'},'id':'worker'}]),answer('写入完成。')]
        events=[e async for e in self.service.stream_turn('请协调已有会话 target 完成文件修复',{'configurable':{'thread_id':'owner'}},approval_handler=lambda *a:'approve_once')]
        self.assertEqual((self.root/'target.txt').read_text(),'native tool path')
        self.assertTrue(any(e.kind=='coordination_result' for e in events))
        usage=[e for e in events if e.kind=='usage']
        self.assertAlmostEqual(self.factory.goal_store.get(self.goal.id).spent_usd,len(usage)*0.000042,places=8,
            msg='coordinator and worker requests must share cumulative goal costs')

    async def test_stale_checkpoint_is_not_replayed_and_local_scope_blocks_other_files(self):
        c=self.coordinator();target=self.factory.task_store.snapshot('target');lease=c.acquire('target',target['revision'],['target.txt'])
        self.assertTrue(c.dispatch('local','target','限定修改',lease['generation'],[])['ok'])
        self.factory.model.i=0;self.factory.model.responses=[answer('',[{'name':'write_file','args':{'path':'other.txt','content':'forbidden'},'id':'escape'}]),answer('未改动。')]
        [e async for e in c.drain(self.service,approval=lambda *a:'approve_once')]
        self.assertFalse((self.root/'other.txt').exists())

    async def test_dependencies_run_serially_and_keep_distinct_task_authorities(self):
        c=self.coordinator()
        self.factory.task_store.begin('second','创建 second.txt')
        [e async for e in self.service.stream_turn('先理解上下文',{'configurable':{'thread_id':'second'}})]
        ids=[]
        for thread in ('target','second'):
            task=self.factory.task_store.snapshot(thread);ids.append(task['task_id'])
            lease=c.acquire(thread,task['revision'],['.'])
            self.assertTrue(c.dispatch(thread,thread,'创建 '+thread+'.txt',lease['generation'],[] if thread=='target' else ['target'])['ok'])
        self.factory.model.i=0;self.factory.model.responses=[
            answer('',[{'name':'write_file','args':{'path':'target.txt','content':'first'},'id':'one'}]),answer('一'),
            answer('',[{'name':'write_file','args':{'path':'second.txt','content':'second'},'id':'two'}]),answer('二')]
        events=[e async for e in c.drain(self.service,approval=lambda *a:'approve_once')]
        self.assertEqual([e.data['request_id'] for e in events if e.kind=='coordination_result'],['target','second'])
        self.assertNotEqual(*ids)
        self.assertEqual((self.root/'second.txt').read_text(),'second')
