import unittest,copy
import test_adaptive_coordination as fixtures
from test_adaptive_coordination import answer

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.CoordinationTests.asyncSetUp
    asyncTearDown=fixtures.CoordinationTests.asyncTearDown
    coordinator=fixtures.CoordinationTests.coordinator

    def queue(self,name='recover'):
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',task['revision'],['.']);self.assertTrue(lease['ok'])
        self.assertTrue(c.dispatch(name,'target','创建 target.txt',lease['generation'],[])['ok'])
        return c

    async def test_user_pause_during_approval_invalidates_before_effect(self):
        c=self.queue()
        self.factory.model.i=0;self.factory.model.responses=[answer('',[{'name':'write_file','args':{'path':'target.txt','content':'stale'},'id':'write'}]),answer('停止。')]
        def approval(*args):c.pause('target');return 'approve_once'
        events=[e async for e in c.drain(self.service,approval=approval)]
        self.assertFalse((self.root/'target.txt').exists())
        self.assertEqual(c.status('recover')['dispatches'][0]['state'],'needs_reconciliation')
        self.assertTrue(any(e.kind=='coordination_result' for e in events))

    async def test_restart_running_without_receipt_is_not_replayed(self):
        c=self.queue();data=c._read();data['dispatches']['recover'].update(state='running',run_id='uncertain');c._write(data)
        fresh=self.coordinator();fresh.reconcile()
        self.assertEqual(fresh.status('recover')['dispatches'][0]['state'],'needs_reconciliation')
        self.assertEqual([e async for e in fresh.drain(self.service)],[])
        self.assertFalse((self.root/'target.txt').exists())

    async def test_known_native_receipt_recovers_completion_without_rebilling(self):
        c=self.queue()
        self.factory.model.i=0;self.factory.model.responses=[answer('',[{'name':'write_file','args':{'path':'target.txt','content':'once'},'id':'write'}]),answer('保存。')]
        [e async for e in c.drain(self.service,approval=lambda *a:'approve_once')]
        spent=self.factory.goal_store.get(self.goal.id).spent_usd
        data=c._read();data['dispatches']['recover']['state']='running';c._write(data)
        fresh=self.coordinator();fresh.reconcile()
        self.assertEqual(fresh.status('recover')['dispatches'][0]['state'],'completed')
        self.assertEqual(self.factory.goal_store.get(self.goal.id).spent_usd,spent)
        self.assertEqual([e async for e in fresh.drain(self.service)],[])

    async def test_real_user_turn_takes_priority_over_queued_controller(self):
        c=self.queue()
        self.factory.model.i=0;self.factory.model.responses=[answer('已更新需求。')]
        [e async for e in self.service.stream_turn('请修改目标，暂时不要创建文件',{'configurable':{'thread_id':'target'}})]
        row=c.status('recover')['dispatches'][0]
        self.assertEqual(row['state'],'cancelled')
        self.assertEqual(c._read()['leases']['target']['state'],'paused')

    async def test_corrupt_state_fails_closed_and_preserves_file(self):
        c=self.queue();raw=c.path.read_text();c.path.write_text('{bad')
        with self.assertRaises(ValueError):c.reconcile()
        self.assertEqual(c.path.read_text(),'{bad');self.assertFalse((self.root/'target.txt').exists())

    async def test_single_session_pause_keeps_independent_dispatch_runnable(self):
        c=self.queue('first')
        self.factory.task_store.begin('second','创建 second.txt')
        [e async for e in self.service.stream_turn('先理解上下文',{'configurable':{'thread_id':'second'}})]
        task=self.factory.task_store.snapshot('second');lease=c.acquire('second',task['revision'],['.'])
        self.assertTrue(c.dispatch('second','second','创建 second.txt',lease['generation'],[])['ok'])
        self.factory.model.i=0;self.factory.model.responses=[
            answer('',[{'name':'write_file','args':{'path':'target.txt','content':'stale'},'id':'one'}]),answer('暂停一'),
            answer('',[{'name':'write_file','args':{'path':'second.txt','content':'independent'},'id':'two'}]),answer('完成二')]
        def approval(action,*args):
            if action['args'].get('path')=='target.txt':c.pause('target')
            return 'approve_once'
        [e async for e in c.drain(self.service,approval=approval)]
        self.assertFalse((self.root/'target.txt').exists());self.assertEqual((self.root/'second.txt').read_text(),'independent')

    async def test_pending_dispatch_blocks_overall_goal_completion(self):
        self.queue()
        ok,message,_=self.factory.goal_store.update(self.goal.id,state='complete',summary='模型声称完成',thread_id='owner')
        self.assertFalse(ok);self.assertIn('派发',message)

    async def test_greeting_or_status_does_not_drain_previously_queued_work(self):
        c=self.queue()
        self.factory.model.i=0;self.factory.model.responses=[answer('你好。')]
        events=[e async for e in self.service.stream_turn('你好',{'configurable':{'thread_id':'owner'}})]
        self.assertFalse(any(e.kind=='coordination_result' for e in events))
        self.assertEqual(c.status('recover')['dispatches'][0]['state'],'queued')

    async def test_coordinator_full_access_cannot_widen_target_default_permissions(self):
        c=self.queue()
        self.service.set_permission_mode('bypassPermissions')
        self.factory.model.i=0;self.factory.model.responses=[answer('',[{'name':'write_file','args':{'path':'target.txt','content':'forbidden'},'id':'write'}]),answer('结束。')]
        [e async for e in c.drain(self.service)]
        self.assertFalse((self.root/'target.txt').exists(),'target default permissions must remain in force')
        self.assertEqual(self.service.permission_mode,'bypassPermissions','coordinator policy must be restored')
