import copy,json,tempfile,unittest
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

    async def test_existing_empty_record_rejects_reads_and_mutations_without_overwrite(self):
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        operations={
            'status':lambda:c.status(),
            'acquire':lambda:c.acquire('target',task['revision'],['.']),
            'pause':lambda:c.pause(),
        }
        for name,operation in operations.items():
            with self.subTest(operation=name):
                c.path.write_bytes(b'{}\n')
                result=operation()
                self.assertFalse(result['ok'],result)
                self.assertEqual(c.path.read_bytes(),b'{}\n')

    async def test_malformed_saved_authorities_reject_status_and_recovery_without_execution(self):
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',task['revision'],['.']);self.assertTrue(lease['ok'])
        self.assertTrue(c.dispatch('saved','target','创建 target.txt',lease['generation'],[])['ok'])
        original=c._read();missing=object()
        common=('owner','goal_id','generation','task_id','revision','scope','checkpoint','permission_mode')
        invalid=[(section,field,missing) for section,fields in (
            ('leases',(*common,'state')),('dispatches',(*common,'request_id','target','stage','dependencies','state','run_id')))
            for field in fields]
        for section in ('leases','dispatches'):
            invalid.extend((section,field,value) for field,value in (
                ('owner',[]),('goal_id',{}),('generation',True),('generation',0),
                ('task_id',[]),('revision','1'),('scope',{}),('scope',[None]),('scope',['../outside']),
                ('checkpoint',{}),('permission_mode',[]),('permission_mode','unknown'),('state',[])))
        invalid.extend([
            ('leases','state','running'),('dispatches','state','unknown'),
            ('dispatches','request_id','different'),('dispatches','target','../foreign'),
            ('dispatches','stage',{}),('dispatches','dependencies',[{}]),('dispatches','dependencies',['missing']),
            ('dispatches','dependencies',['saved']),('dispatches','run_id',{}),('dispatches','result',[]),
            ('dispatches','owner','other'),('dispatches','goal_id','other'),('dispatches','task_id','other'),
            ('dispatches','scope',['different.txt']),('dispatches','generation',lease['generation']+1),
        ])
        model_index=self.factory.model.i
        for section,field,value in invalid:
            with self.subTest(section=section,field=field,value=value):
                data=copy.deepcopy(original);row=data[section]['target' if section=='leases' else 'saved']
                if value is missing:row.pop(field)
                else:row[field]=value
                raw=json.dumps(data).encode();c.path.write_bytes(raw)
                self.assertFalse(c.status()['ok'])
                with self.assertRaises(ValueError):c.reconcile()
                with self.assertRaises(ValueError):[event async for event in c.drain(self.service)]
                self.assertEqual(c.path.read_bytes(),raw)
                self.assertEqual(self.factory.model.i,model_index)
                self.assertFalse((self.root/'target.txt').exists())

    async def test_saved_dispatch_states_keep_optional_result_compatibility(self):
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',task['revision'],['.'])
        self.assertTrue(c.dispatch('saved','target','创建 target.txt',lease['generation'],[])['ok'])
        original=c._read()
        for state in ('queued','admitted','running','completed','partial','failed','cancelled','needs_reconciliation'):
            with self.subTest(state=state):
                data=copy.deepcopy(original);row=data['dispatches']['saved'];row['state']=state
                row['run_id']=None if state in {'queued','cancelled'} else 'native-run'
                row.pop('result');raw=json.dumps(data).encode();c.path.write_bytes(raw)
                self.assertTrue(c.status()['ok'])
                self.assertEqual(c.path.read_bytes(),raw)

    async def test_excessive_saved_dispatches_reject_recovery_without_overwrite(self):
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',task['revision'],['.'])
        self.assertTrue(c.dispatch('saved','target','创建 target.txt',lease['generation'],[])['ok'])
        data=c._read();row=data['dispatches']['saved']
        data['dispatches']={str(index):{**row,'request_id':str(index)} for index in range(201)}
        raw=json.dumps(data).encode();c.path.write_bytes(raw)
        self.assertFalse(c.status()['ok'])
        with self.assertRaises(ValueError):c.reconcile()
        self.assertEqual(c.path.read_bytes(),raw)

    async def test_two_successful_dependent_stages_do_not_pause_goal_as_idle(self):
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',task['revision'],['.'])
        self.assertTrue(c.dispatch('first','target','创建 target.txt',lease['generation'],[])['ok'])
        self.assertTrue(c.dispatch('second','target','创建 second.txt',lease['generation'],['first'])['ok'])
        self.factory.model.i=0;self.factory.model.responses=[
            answer('',[{'name':'write_file','args':{'path':'target.txt','content':'first stage'},'id':'first-write'}]),answer('第一阶段完成。'),
            answer('',[{'name':'write_file','args':{'path':'second.txt','content':'second stage'},'id':'second-write'}]),answer('第二阶段完成。')]
        events=[event async for event in c.drain(self.service,approval=lambda *args:'approve_once')]
        self.assertEqual((self.root/'target.txt').read_text(),'first stage')
        self.assertEqual((self.root/'second.txt').read_text(),'second stage')
        self.assertEqual([event.data['request_id'] for event in events if event.kind=='coordination_result'],['first','second'])
        goal=self.factory.goal_store.get(self.goal.id)
        self.assertEqual(goal.round,2);self.assertEqual(goal.state,'active');self.assertEqual(goal.idle_rounds,0)

    async def test_recovered_successful_receipt_counts_observed_tools_as_progress(self):
        self.factory.goal_store.record_round(self.goal.id,cost_usd=0,files_changed=False,tool_calls=0,round_id='idle-before-dispatch')
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',task['revision'],['.'])
        self.assertTrue(c.dispatch('recover','target','创建 target.txt',lease['generation'],[])['ok'])
        self.factory.model.i=0;self.factory.model.responses=[
            answer('',[{'name':'write_file','args':{'path':'target.txt','content':'confirmed output'},'id':'write'}]),answer('已保存。')]
        with patch.object(self.factory.goal_store,'record_round',side_effect=OSError('settlement unavailable')):
            with self.assertRaises(OSError):[event async for event in c.drain(self.service,approval=lambda *args:'approve_once')]
        self.assertEqual((self.root/'target.txt').read_text(),'confirmed output')
        self.assertEqual(c.status('recover')['dispatches'][0]['state'],'running')

        self.coordinator().reconcile()

        goal=self.factory.goal_store.get(self.goal.id)
        self.assertEqual(c.status('recover')['dispatches'][0]['state'],'completed')
        self.assertEqual(goal.round,2);self.assertEqual(goal.state,'active');self.assertEqual(goal.idle_rounds,0)
        self.assertGreater(goal.spent_usd,0)

    async def test_legacy_receipt_without_observed_tools_does_not_infer_progress_or_rebill(self):
        from nailong.core.verification import input_fingerprint
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',task['revision'],['.'])
        self.assertTrue(c.dispatch('legacy','target','读取 target.txt',lease['generation'],[])['ok'])
        data=c._read();row=data['dispatches']['legacy'];row.update(state='running',run_id='legacy-native-run');c._write(data)
        self.factory.session_store.append_event('target','coordination_receipt',{
            'request_id':'legacy','run_id':'legacy-native-run','task_id':task['task_id'],'revision':task['revision'],
            'generation':lease['generation'],'state':'completed','usage_complete':True,
            'budget_round_id':'legacy-native-round','standalone':True,'cost_usd':0.001,
            'checkpoint':row['checkpoint'],'input_fingerprint':input_fingerprint(self.root),'result':{},
        })

        c.reconcile();c.reconcile()

        goal=self.factory.goal_store.get(self.goal.id)
        self.assertEqual(c.status('legacy')['dispatches'][0]['state'],'completed')
        self.assertEqual(goal.round,1);self.assertEqual(goal.state,'active');self.assertEqual(goal.idle_rounds,1)
        self.assertAlmostEqual(goal.spent_usd,0.001)

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

    async def test_receipt_fingerprint_keeps_event_loop_responsive(self):
        import asyncio,threading
        from nailong.core.verification import input_fingerprint
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',task['revision'],['.'])
        self.assertTrue(c.dispatch('responsive','target','创建 target.txt',lease['generation'],[])['ok'])
        self.factory.model.i=0;self.factory.model.responses=[
            answer('',[{'name':'write_file','args':{'path':'target.txt','content':'saved'},'id':'write'}]),answer('完成。')]
        loop=asyncio.get_running_loop();observed=[]
        def observe(root):
            tick=threading.Event();loop.call_soon_threadsafe(tick.set)
            observed.append(tick.wait(.5))
            return input_fingerprint(root)
        with patch('nailong.core.session_coordination.input_fingerprint',side_effect=observe):
            [event async for event in c.drain(self.service,approval=lambda *args:'approve_once')]
        self.assertEqual(observed,[True],'receipt scanning blocked the owning event loop')
        self.assertEqual(c.status('responsive')['dispatches'][0]['state'],'completed')

    async def test_closing_dispatch_stream_settles_once_without_yielding_during_close(self):
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',task['revision'],['.'])
        self.assertTrue(c.dispatch('close','target','调查',lease['generation'],[])['ok'])
        original_mode=self.service.permission_mode
        events=c.drain(self.service)
        async for event in events:
            if event.kind=='usage':break
        await events.aclose()
        await events.aclose()
        self.assertEqual(self.service.permission_mode,original_mode)
        self.assertEqual(c.status('close')['dispatches'][0]['state'],'needs_reconciliation')
        goal=self.factory.goal_store.get(self.goal.id)
        self.assertEqual(goal.round,1)
        self.assertGreater(goal.spent_usd,0)
        self.assertEqual(goal.state,'paused')

    async def test_cancel_during_receipt_scan_retains_cost_and_restores_execution(self):
        import asyncio,threading
        from nailong.core.verification import input_fingerprint
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',task['revision'],['.'])
        self.assertTrue(c.dispatch('scan-cancel','target','调查',lease['generation'],[])['ok'])
        loop=asyncio.get_running_loop();entered=asyncio.Event();release=threading.Event()
        def scan(root):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(2):raise AssertionError('test did not release receipt scan')
            return input_fingerprint(root)
        async def consume():return [event async for event in c.drain(self.service)]
        original_mode=self.service.permission_mode
        with patch('nailong.core.session_coordination.input_fingerprint',side_effect=scan):
            pending=asyncio.create_task(consume())
            try:
                await asyncio.wait_for(entered.wait(),2)
                pending.cancel();release.set()
                with self.assertRaises(asyncio.CancelledError):await pending
            finally:
                release.set()
                if not pending.done():pending.cancel()
        self.assertEqual(self.service.permission_mode,original_mode)
        self.assertEqual(c.status('scan-cancel')['dispatches'][0]['state'],'needs_reconciliation')
        receipts=[e['data'] for e in self.factory.session_store.read_events('target') if e['kind']=='coordination_receipt']
        self.assertEqual(len(receipts),1)
        self.assertIsNone(receipts[0]['input_fingerprint'])
        goal=self.factory.goal_store.get(self.goal.id)
        self.assertEqual(goal.round,1);self.assertGreater(goal.spent_usd,0);self.assertEqual(goal.state,'paused')

    async def test_revision_change_during_receipt_scan_cannot_report_completion(self):
        import asyncio,threading
        from nailong.core.verification import input_fingerprint
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',task['revision'],['.'])
        self.assertTrue(c.dispatch('scan-revision','target','创建 target.txt',lease['generation'],[])['ok'])
        self.factory.model.i=0;self.factory.model.responses=[
            answer('',[{'name':'write_file','args':{'path':'target.txt','content':'saved'},'id':'write'}]),answer('完成。')]
        loop=asyncio.get_running_loop();changed=threading.Event()
        def change():
            self.factory.task_store.amend('target',objective='用户已经变更目标')
            changed.set()
        def scan(root):
            value=input_fingerprint(root)
            loop.call_soon_threadsafe(change)
            if not changed.wait(2):raise AssertionError('revision change blocked by scan')
            return value
        with patch('nailong.core.session_coordination.input_fingerprint',side_effect=scan):
            events=[event async for event in c.drain(self.service,approval=lambda *args:'approve_once')]
        self.assertEqual(c.status('scan-revision')['dispatches'][0]['state'],'needs_reconciliation')
        self.assertNotIn('completed',[e.data['state'] for e in events if e.kind=='coordination_result'])
        self.assertEqual(self.factory.goal_store.get(self.goal.id).round,1)

    async def test_receipt_checkpoint_failure_still_settles_observed_usage(self):
        from nailong.core.session_coordination import checkpoint
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',task['revision'],['.'])
        self.assertTrue(c.dispatch('checkpoint-fail','target','创建 target.txt',lease['generation'],[])['ok'])
        self.factory.model.i=0;self.factory.model.responses=[
            answer('',[{'name':'write_file','args':{'path':'target.txt','content':'saved'},'id':'write'}]),answer('完成。')]
        def fail_at_receipt(factory,thread):
            if (self.root/'target.txt').exists():raise ValueError('checkpoint unavailable at closeout')
            return checkpoint(factory,thread)
        with patch('nailong.core.session_coordination.checkpoint',side_effect=fail_at_receipt):
            [event async for event in c.drain(self.service,approval=lambda *args:'approve_once')]
        self.assertEqual(c.status('checkpoint-fail')['dispatches'][0]['state'],'needs_reconciliation')
        goal=self.factory.goal_store.get(self.goal.id)
        self.assertEqual(goal.round,1);self.assertGreater(goal.spent_usd,0)

    async def test_async_recovery_rechecks_revision_after_scanning_saved_receipt(self):
        import asyncio,threading
        from nailong.core.verification import input_fingerprint
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',task['revision'],['.'])
        self.assertTrue(c.dispatch('scan-recover','target','创建 target.txt',lease['generation'],[])['ok'])
        self.factory.model.i=0;self.factory.model.responses=[
            answer('',[{'name':'write_file','args':{'path':'target.txt','content':'saved'},'id':'write'}]),answer('完成。')]
        with patch.object(self.factory.goal_store,'record_round',side_effect=OSError('settlement unavailable')):
            with self.assertRaises(OSError):
                [event async for event in c.drain(self.service,approval=lambda *args:'approve_once')]
        loop=asyncio.get_running_loop();changed=threading.Event()
        def change():
            self.factory.task_store.amend('target',objective='用户已经变更目标')
            changed.set()
        def scan(root):
            value=input_fingerprint(root)
            loop.call_soon_threadsafe(change)
            if not changed.wait(2):raise AssertionError('recovery scan blocked revision change')
            return value
        with patch('nailong.core.session_coordination.input_fingerprint',side_effect=scan):
            [event async for event in c.drain(self.service)]
        self.assertEqual(c.status('scan-recover')['dispatches'][0]['state'],'needs_reconciliation')
        self.assertEqual(self.factory.goal_store.get(self.goal.id).state,'paused')

    async def test_missing_final_checkpoint_preserves_valid_lease(self):
        from nailong.core.session_coordination import checkpoint
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',task['revision'],['.'])
        self.assertTrue(c.dispatch('checkpoint-missing','target','创建 target.txt',lease['generation'],[])['ok'])
        before=c._read()['leases']['target']['checkpoint']
        self.factory.model.i=0;self.factory.model.responses=[
            answer('',[{'name':'write_file','args':{'path':'target.txt','content':'saved'},'id':'write'}]),answer('完成。')]
        def missing_at_receipt(factory,thread):
            return None if (self.root/'target.txt').exists() else checkpoint(factory,thread)
        with patch('nailong.core.session_coordination.checkpoint',side_effect=missing_at_receipt):
            [event async for event in c.drain(self.service,approval=lambda *args:'approve_once')]
        status=c.status('checkpoint-missing')
        self.assertTrue(status['ok'],status)
        self.assertEqual(status['dispatches'][0]['state'],'needs_reconciliation')
        self.assertEqual(c._read()['leases']['target']['checkpoint'],before)
        self.assertEqual(self.factory.goal_store.get(self.goal.id).round,1)

    async def test_async_recovery_accepts_already_billed_budget_pause_without_rebilling(self):
        c=self.coordinator()
        with self.factory.goal_store._locked():
            saved=self.factory.goal_store._load();saved[self.goal.id].max_rounds=1
            self.factory.goal_store._save(saved)
        task=self.factory.task_store.snapshot('target');lease=c.acquire('target',task['revision'],['.'])
        self.assertTrue(c.dispatch('billed-recover','target','创建 target.txt',lease['generation'],[])['ok'])
        self.factory.model.i=0;self.factory.model.responses=[
            answer('',[{'name':'write_file','args':{'path':'target.txt','content':'saved'},'id':'write'}]),answer('完成。')]
        transition=c._transition
        def fail_after_billing(row,state,result=None):
            if state=='completed':raise OSError('crash after cost settlement')
            return transition(row,state,result)
        with patch.object(c,'_transition',side_effect=fail_after_billing):
            with self.assertRaises(OSError):
                [event async for event in c.drain(self.service,approval=lambda *args:'approve_once')]
        goal=self.factory.goal_store.get(self.goal.id)
        self.assertEqual(goal.state,'paused');self.assertEqual(goal.round,1)
        [event async for event in c.drain(self.service)]
        self.assertEqual(c.status('billed-recover')['dispatches'][0]['state'],'completed')
        recovered=self.factory.goal_store.get(self.goal.id)
        self.assertEqual(recovered.round,goal.round);self.assertEqual(recovered.spent_usd,goal.spent_usd)
        self.assertEqual(recovered.state,'paused')

    async def test_unexpected_receipt_scan_failure_releases_lease_with_failed_task_still_referenced(self):
        import asyncio
        c=self.coordinator();task=self.factory.task_store.snapshot('target')
        lease=c.acquire('target',task['revision'],['.'])
        self.assertTrue(c.dispatch('scan-error','target','创建 target.txt',lease['generation'],[])['ok'])
        self.factory.model.i=0;self.factory.model.responses=[
            answer('',[{'name':'write_file','args':{'path':'target.txt','content':'saved'},'id':'write'}]),answer('完成。')]
        async def consume():return [event async for event in c.drain(self.service,approval=lambda *args:'approve_once')]
        with patch('nailong.core.session_coordination.input_fingerprint',side_effect=RuntimeError('unexpected scan failure')):
            failed=asyncio.create_task(consume())
            caught=None
            try:await failed
            except RuntimeError as error:caught=error
            self.assertIsInstance(caught,RuntimeError)
            self.assertIn('unexpected scan failure',str(caught))
        self.assertTrue(failed.done())
        self.assertEqual((self.root/'target.txt').read_text(),'saved')
        spent=self.factory.goal_store.get(self.goal.id).spent_usd
        self.assertGreater(spent,0)
        self.assertEqual(self.factory.goal_store.costs.recover(),[self.goal.id])
        self.assertEqual(self.factory.goal_store.get(self.goal.id).spent_usd,spent)
