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

    async def test_failed_verification_cannot_complete_dispatch_or_release_dependents(self):
        from nailong.core.preferences import atomic_json
        root=self.factory.task_store.project_root
        config=root/'.nailong/settings.json'
        atomic_json(config,{'verification':{'steps':[{'name':'must-pass','kind':'test','command':'false'}]}},root)
        # Reconcile the user-configured verification before freezing dispatch authority.
        from nailong.core.verification import VerificationService
        self.factory.task_store.configure_verification('target',VerificationService(self.root,self.factory.session_store).list_steps())
        c=self.queue('first')
        task=self.factory.task_store.snapshot('target');generation=c._read()['leases']['target']['generation']
        self.assertTrue(c.dispatch('dependent','target','接着修改文件',generation,['first'])['ok'])
        self.factory.model.i=0;self.factory.model.responses=[answer('请执行验证。')]
        [e async for e in c.drain(self.service,approval=lambda *a:'approve_once')]
        rows={row['request_id']:row for row in c.status()['dispatches']}
        self.assertEqual(rows['first']['state'],'failed',rows['first'])
        self.assertEqual(rows['dependent']['state'],'queued')

    async def test_missing_worker_usage_remains_unknown_in_owner_bill(self):
        from langchain_core.messages import AIMessage
        c=self.queue()
        self.factory.model.i=0;self.factory.model.responses=[AIMessage(content='用量缺失。')]
        [e async for e in c.drain(self.service)]
        summary=self.service.get_usage_summary('owner')
        self.assertFalse(summary['cumulative']['usage_complete'],summary)
        self.assertFalse(summary['cumulative']['cost_complete'],summary)

    async def test_automatic_verification_respects_target_stage_restriction(self):
        import shlex,sys
        from nailong.core.preferences import atomic_json
        from nailong.core.verification import VerificationService
        root=self.factory.task_store.project_root
        (root/'check.py').write_text("from pathlib import Path;Path('forbidden.txt').write_text('effect')")
        self.factory.task_store.begin('target','stage: design\nonly run check.py in stage implementation')
        atomic_json(root/'.nailong/settings.json',{'verification':{'steps':[{'name':'restricted','kind':'test','command':shlex.quote(sys.executable)+' check.py'}]}},root)
        self.factory.task_store.configure_verification('target',VerificationService(root,self.factory.session_store).list_steps())
        c=self.queue()
        self.factory.model.i=0;self.factory.model.responses=[answer('准备验证。')]
        [e async for e in c.drain(self.service,approval=lambda *a:'approve_once')]
        self.assertFalse((root/'forbidden.txt').exists(),'automatic verification must not bypass stage constraints')

    async def test_recovery_cannot_restore_completion_after_artifact_changed(self):
        c=self.queue()
        self.factory.model.i=0;self.factory.model.responses=[answer('',[{'name':'write_file','args':{'path':'target.txt','content':'once'},'id':'write'}]),answer('保存。')]
        [e async for e in c.drain(self.service,approval=lambda *a:'approve_once')]
        data=c._read();data['dispatches']['recover']['state']='running';c._write(data)
        (self.root/'target.txt').write_text('user edit after execution')
        fresh=self.coordinator();fresh.reconcile()
        self.assertEqual(fresh.status('recover')['dispatches'][0]['state'],'needs_reconciliation')
        self.assertEqual((self.root/'target.txt').read_text(),'user edit after execution')

    async def test_unverified_prose_only_stage_cannot_release_dependencies(self):
        c=self.queue('first');generation=c._read()['leases']['target']['generation']
        self.assertTrue(c.dispatch('dependent','target','使用前一阶段产物',generation,['first'])['ok'])
        self.factory.model.i=0;self.factory.model.responses=[answer('模型声称已经修复，但没有执行或验证。')]
        [e async for e in c.drain(self.service)]
        rows={row['request_id']:row for row in c.status()['dispatches']}
        self.assertEqual(rows['first']['state'],'partial')
        self.assertEqual(rows['dependent']['state'],'queued')

    async def test_gui_new_instruction_invalidates_lease_at_enqueue_before_execution(self):
        from nailong.gui.controller import GUIController
        c=self.queue();controller=GUIController(self.root,sessions=self.factory.session_store,service=self.service)
        try:
            branch=next(b for b in controller.branches.branches() if b['thread_id']=='target')
            controller.paused=True
            queued=await controller.command('send',{'branch_id':branch['id'],'message':'请修改目标，先不要创建文件','request_id':'intervene-at-enqueue'})
            self.assertEqual(controller.runs[queued['run_id']]['status'],'queued')
            self.assertEqual(c._read()['leases']['target']['state'],'paused')
            self.assertEqual(c.status('recover')['dispatches'][0]['state'],'cancelled')
        finally:await controller.close()
