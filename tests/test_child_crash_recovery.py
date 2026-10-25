"""Child execution ownership and native progress survive actual process exit."""
import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import test_adaptive_child_continuations as fixtures
from nailong.core.child_continuations import ChildContinuations
from nailong.tools.agents import ChildTaskResult,ReadOnlyTaskRunner


class ChildCrashRecoveryTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.ChildContinuationTests.setUp

    def factory(self,responses,*,session_token_limit=None):
        from agent import AgentRuntimeFactory
        from config import Settings
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        class OfflineModel(FakeMessagesListChatModel):
            def bind_tools(self,tools,**kwargs):return self
        home=Path(self.tmp.name)/'home';home.mkdir(exist_ok=True)
        mocked=patch('pathlib.Path.home',return_value=home);mocked.start();self.addCleanup(mocked.stop)
        factory=AgentRuntimeFactory(Settings('fixture-key','https://fixture.invalid','deepseek-flash',self.root),
            session_store=self.sessions,session_token_limit=session_token_limit)
        factory.model=OfflineModel(responses=responses)
        async def close():await factory.aclose();factory.close()
        self.addAsyncCleanup(close)
        return factory

    def tools(self,factory):
        from tools import build_tools
        from nailong.tools.files import FileSession
        return {tool.name:tool for tool in build_tools(file_session=FileSession(self.root),
            execution_context=factory.tool_execution_context,task_runner=factory.task_runner,thread_id='parent')}

    async def invoke(self,tool,arguments):
        result=await tool.ainvoke({'type':'tool_call','name':tool.name,'id':'native-call','args':arguments},
            config={'configurable':{'thread_id':'parent'}})
        return json.loads(result.content)

    def crash(self, *, native=False, budget=False, resume=''):
        code='''import asyncio,os,sys
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
from nailong.core.child_continuations import ChildContinuations
from nailong.tools.agents import ReadOnlyTaskRunner
sessions=ProjectSessionStore(sys.argv[1],base_dir=sys.argv[2]);tasks=TaskStore(sessions)
if sys.argv[3].startswith('native'):
    from unittest.mock import patch
    from pathlib import Path
    home=Path(sys.argv[2])/'home';home.mkdir();patch('pathlib.Path.home',return_value=home).start()
    from agent import AgentRuntimeFactory
    from config import Settings
    from langchain_core.messages import AIMessage
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    class CrashAfterRead(FakeMessagesListChatModel):
        def bind_tools(self,tools,**kwargs):return self
        def _generate(self,messages,stop=None,run_manager=None,**kwargs):
            if sys.argv[3]=='native-budget':
                with (Path(sys.argv[2])/'provider.calls').open('a') as stream:stream.write('called\\n')
            if any(getattr(m,'type',None)=='tool' for m in messages):os._exit(29)
            return super()._generate(messages,stop=stop,run_manager=run_manager,**kwargs)
    factory=AgentRuntimeFactory(Settings('fixture-key','https://fixture.invalid','deepseek-flash',sessions.project_root),
        session_store=sessions,session_token_limit=100000 if sys.argv[3]=='native-budget' else None)
    factory.model=CrashAfterRead(responses=[AIMessage(content='',tool_calls=[{'name':'read_file','args':{'path':'a.txt','max_chars':40},'id':'page-one'}],usage_metadata={'input_tokens':100,'output_tokens':10,'total_tokens':110})])
    runner=factory.task_runner;store=runner.continuations
else:
    async def worker(prompt):os._exit(29)
    runner=ReadOnlyTaskRunner(worker);store=ChildContinuations(sessions,tasks)
async def run():
    if sys.argv[3]=='native-budget':
        from nailong.core.budgets import GoalCostBudget,active_cost_budget
        from nailong.core.cumulative_budget import active_session_token_budget
        from nailong.core.costs import CostEstimator
        goal=factory.goal_store.create(tasks.snapshot('parent')['objective'],thread_id='parent',max_cost_usd=1)
        dollars=GoalCostBudget(CostEstimator(factory.settings.model,sessions.project_root),1,
            goal_store=factory.goal_store,goal_id=goal.id,round_id='native-child-round')
        dollar_token=active_cost_budget.set(dollars)
        session_token=active_session_token_budget.set(factory.session_token_budget('parent'))
        try:return await store.run(runner,'parent','研究 a.txt')
        finally:
            active_session_token_budget.reset(session_token);active_cost_budget.reset(dollar_token);dollars.close()
    return await store.run(runner,'parent','研究 a.txt',sys.argv[4])
asyncio.run(run())
'''
        result=subprocess.run([sys.executable,'-c',code,str(self.root),str(Path(self.tmp.name)/'data'),
            'native-budget' if budget else 'native' if native else 'worker',resume],capture_output=True,timeout=15)
        self.assertEqual(result.returncode,29,result.stderr.decode())
        store=ChildContinuations(self.sessions,self.tasks)
        rows=store._read()['runs'];self.assertEqual(len(rows),2 if resume else 1)
        return store,next(identity for identity,row in rows.items() if row['status']=='running')

    async def test_actual_exit_recovers_once_without_starting_a_worker_and_explicit_resume_works(self):
        store,reference=self.crash();before=self.tasks.snapshot('parent')
        self.assertEqual(store.recover(),[reference])
        recovered=store._read()['runs'][reference]
        self.assertEqual(recovered['status'],'interrupted');self.assertFalse(recovered['usage_complete'])
        self.assertIn('process_interrupted',recovered['stop_reason'])
        saved=store.path.read_bytes();self.assertEqual(store.recover(),[])
        self.assertEqual(store.path.read_bytes(),saved);self.assertEqual(self.tasks.snapshot('parent'),before)
        calls=[]
        async def worker(prompt):calls.append(prompt);return ChildTaskResult('继续完成',{'input_tokens':1,'output_tokens':1})
        result=await store.run(ReadOnlyTaskRunner(worker),'parent','继续',reference)
        self.assertTrue(result['ok'],result);self.assertEqual(len(calls),1)
        self.assertEqual(result['parent_run_id'],reference)
        self.assertFalse((await store.run(ReadOnlyTaskRunner(worker),'parent','重复',reference))['ok'])

    async def test_live_execution_is_not_recovered_and_lease_is_released_on_return(self):
        store=ChildContinuations(self.sessions,self.tasks);entered=asyncio.Event();release=asyncio.Event()
        async def worker(prompt):
            entered.set();await release.wait();return ChildTaskResult('阶段',{'input_tokens':1,'output_tokens':1})
        pending=asyncio.create_task(store.run(ReadOnlyTaskRunner(worker),'parent','研究'))
        try:
            await asyncio.wait_for(entered.wait(),2)
            other=ChildContinuations(self.sessions,self.tasks)
            self.assertEqual(other.recover(),[])
            row=next(iter(other._read()['runs'].values()))
            self.assertEqual(row['status'],'running')
            with self.assertRaises(BlockingIOError):
                with other._lease(row['run_id']):pass
        finally:release.set();result=await pending
        with other._lease(result['resume_reference']):pass
        self.assertEqual(other.recover(),[])

    async def test_closeout_failure_releases_lease_even_when_traceback_is_retained(self):
        store=ChildContinuations(self.sessions,self.tasks);original=store._write;calls=[]
        def fail_final(data):
            calls.append(None)
            if len(calls)>1:raise OSError('closeout unavailable')
            original(data)
        async def worker(prompt):return ChildTaskResult('阶段',{'input_tokens':1,'output_tokens':1})
        with patch.object(store,'_write',side_effect=fail_final):
            result=await store.run(ReadOnlyTaskRunner(worker),'parent','研究')
        self.assertFalse(result['ok']);self.assertTrue(result['started'])
        reference=next(iter(store._read()['runs']))
        with store._lease(reference):pass
        self.assertEqual(store.recover(),[reference])

    async def test_legacy_running_has_unknown_owner_and_cannot_be_automatically_resumed(self):
        store,reference=self.crash();data=store._read();data['runs'][reference].pop('lease_version',None)
        store._write(data);self.assertEqual(store.recover(),[reference])
        row=store._read()['runs'][reference]
        self.assertEqual(row['status'],'usage_unknown');self.assertFalse(row['usage_complete'])
        calls=[]
        async def worker(prompt):calls.append(prompt);return 'must not run'
        result=await store.run(ReadOnlyTaskRunner(worker),'parent','继续',reference)
        self.assertFalse(result['ok']);self.assertEqual(calls,[])

    async def test_status_pages_only_current_thread_and_labels_unchecked_inputs(self):
        store,reference=self.crash()
        self.tasks.begin('other','其他任务')
        async def worker(prompt):return ChildTaskResult('阶段',{'input_tokens':1,'output_tokens':1})
        await store.run(ReadOnlyTaskRunner(worker),'other','other')
        page=store.status('parent',limit=1)
        self.assertTrue(page['ok'],page);self.assertEqual(len(page['runs']),1)
        self.assertEqual(page['runs'][0]['resume_reference'],reference)
        self.assertTrue(page['historical']);self.assertEqual(page['input_status'],'not_checked')
        self.assertFalse(store.status('other',reference)['ok'])
        detail=store.status('parent',reference)
        self.assertEqual(detail['runs'][0]['status'],'interrupted')
        self.tasks.amend('parent',constraints=['new requirement'])
        self.assertFalse(store.status('parent',reference)['runs'][0]['current_task'])

    async def test_native_read_checkpoint_survives_exit_before_final_child_result(self):
        (self.root/'a.txt').write_text('x'*20000)
        store,reference=self.crash(native=True)
        running=store._read()['runs'][reference]
        self.assertEqual(running['status'],'running')
        self.assertEqual(len(running['evidence']),1)
        page=running['evidence'][0]
        self.assertEqual((page['tool'],page['path'],page['call_id']),('read_file','a.txt','page-one'))
        self.assertTrue(page['truncated']);self.assertEqual(page['next_char_offset'],40)
        store.recover();recovered=store.status('parent',reference)['runs'][0]
        self.assertEqual(recovered['resume_position']['char_offset'],40)
        self.assertEqual(recovered['resume_position']['version'],page['version'])

    async def test_other_process_live_lease_is_skipped_and_kill_is_recoverable(self):
        ready,release=Path(self.tmp.name)/'ready',Path(self.tmp.name)/'release'
        code='''import asyncio,sys
from pathlib import Path
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
from nailong.core.child_continuations import ChildContinuations
from nailong.tools.agents import ReadOnlyTaskRunner,ChildTaskResult
sessions=ProjectSessionStore(sys.argv[1],base_dir=sys.argv[2]);tasks=TaskStore(sessions)
async def worker(prompt):
    Path(sys.argv[3]).write_text('ready')
    while not Path(sys.argv[4]).exists():await asyncio.sleep(.01)
    return ChildTaskResult('done',{'input_tokens':1,'output_tokens':1})
asyncio.run(ChildContinuations(sessions,tasks).run(ReadOnlyTaskRunner(worker),'parent','research'))
'''
        process=subprocess.Popen([sys.executable,'-c',code,str(self.root),str(Path(self.tmp.name)/'data'),
            str(ready),str(release)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+5
            while not ready.exists() and time.monotonic()<deadline and process.poll() is None:await asyncio.sleep(.01)
            self.assertTrue(ready.exists())
            store=ChildContinuations(self.sessions,self.tasks);reference=next(iter(store._read()['runs']))
            self.assertEqual(store.recover(),[])
            self.assertEqual(store._read()['runs'][reference]['status'],'running')
            process.kill();await asyncio.to_thread(process.communicate,timeout=5)
            self.assertEqual(store.recover(),[reference])
            self.assertEqual(store._read()['runs'][reference]['status'],'interrupted')
        finally:
            if process.poll() is None:process.kill()
            process.communicate(timeout=5)

    async def test_restart_native_status_tool_finds_progress_and_explicit_task_continues_page(self):
        from langchain_core.messages import AIMessage
        from nailong.core.capabilities import CapabilityCatalog
        (self.root/'a.txt').write_text('x'*20000)
        store,reference=self.crash(native=True)
        usage={'input_tokens':100,'output_tokens':10,'total_tokens':110}
        factory=self.factory([AIMessage(content='',tool_calls=[{'name':'read_file',
            'args':{'path':'a.txt','char_offset':40,'max_chars':40},'id':'page-two'}],usage_metadata=usage),
            AIMessage(content='已补读下一页，文件仍未读完。',usage_metadata=usage)])
        self.assertEqual(store._read()['runs'][reference]['status'],'interrupted')
        tools=self.tools(factory);catalog=CapabilityCatalog();catalog.register(list(tools.values()))
        self.assertIn('task_status',[row['name'] for row in catalog.search('task',limit=10)['tools']])
        status=await self.invoke(tools['task_status'],{'resume_reference':reference})
        self.assertEqual(status['runs'][0]['resume_position']['char_offset'],40)
        result=await self.invoke(tools['task'],{'description':'按已保存位置继续补读','resume_reference':reference})
        self.assertTrue(result['ok'],result);self.assertEqual(result['child_status'],'partial')
        self.assertEqual(result['parent_run_id'],reference)
        self.assertEqual(result['resume_position']['char_offset'],80)
        self.assertEqual(len(result['evidence']),1,'checkpoint and final result must not duplicate a call')

    async def test_checkpoint_write_failure_stops_child_before_next_model_call(self):
        from langchain_core.messages import AIMessage
        usage={'input_tokens':100,'output_tokens':10,'total_tokens':110}
        factory=self.factory([AIMessage(content='',tool_calls=[{'name':'read_file',
            'args':{'path':'a.txt'},'id':'read'}],usage_metadata=usage),AIMessage(content='must not run',usage_metadata=usage)])
        store=factory.task_runner.continuations;original=store._write;calls=[]
        def fail_checkpoint(data):
            calls.append(None)
            if len(calls)==2:raise OSError('progress unavailable')
            return original(data)
        with patch.object(store,'_write',side_effect=fail_checkpoint):
            result=await self.invoke(self.tools(factory)['task'],{'description':'调查 a.txt'})
        self.assertEqual(result['child_status'],'usage_unknown')
        self.assertEqual(factory.model.i,1,'the second model response must not be consumed')
        self.assertFalse(result['usage_complete'])
        self.assertGreater(result['usage']['total_tokens'],0)
        with store._lease(result['resume_reference']):pass

    async def test_native_repeated_cancellation_preserves_read_progress_and_releases_lease(self):
        from langchain_core.messages import AIMessage
        factory=self.factory([AIMessage(content='',tool_calls=[{'name':'read_file',
            'args':{'path':'a.txt','max_chars':2},'id':'read'}],
            usage_metadata={'input_tokens':100,'output_tokens':10,'total_tokens':110})])
        entered=asyncio.Event();model=factory.model;original=model._agenerate
        async def block_after_read(messages,*args,**kwargs):
            if any(getattr(message,'type',None)=='tool' for message in messages):
                entered.set();await asyncio.Event().wait()
            return await original(messages,*args,**kwargs)
        with patch.object(type(model),'_agenerate',side_effect=block_after_read):
            pending=asyncio.create_task(self.invoke(self.tools(factory)['task'],{'description':'调查 a.txt'}))
            try:
                await asyncio.wait_for(entered.wait(),3)
                pending.cancel();await asyncio.sleep(0);pending.cancel()
                with self.assertRaises(asyncio.CancelledError):await pending
            finally:
                if not pending.done():pending.cancel()
                await asyncio.gather(pending,return_exceptions=True)
        store=factory.task_runner.continuations;row=next(iter(store._read()['runs'].values()))
        self.assertEqual(row['status'],'interrupted');self.assertEqual(len(row['evidence']),1)
        self.assertFalse(row['usage_complete'])
        self.assertEqual(factory.task_runner._budget('parent').reserved,0)
        with store._lease(row['run_id']):pass

    async def test_large_child_result_keeps_stage_and_resume_identity_in_bounded_tool_response(self):
        from tools import build_tools
        from nailong.tools.files import FileSession
        async def worker(prompt):
            return ChildTaskResult('x'*20000,{'input_tokens':1,'output_tokens':1},
                budget_failure={'reason':'stage'})
        runner=ReadOnlyTaskRunner(worker);runner.continuations=ChildContinuations(self.sessions,self.tasks)
        tool=next(tool for tool in build_tools(file_session=FileSession(self.root),task_runner=runner,
            thread_id='parent') if tool.name=='task')
        result=await self.invoke(tool,{'description':'research'})
        self.assertTrue(result['result_truncated'])
        self.assertEqual(result['child_status'],'budget_exhausted')
        self.assertIn(result['resume_reference'],runner.continuations._read()['runs'])
        self.assertEqual(result['status'],'success','tool execution and child completion are separate fields')

    async def test_malformed_progress_is_rejected_before_recovery_without_overwriting_bytes(self):
        store,reference=self.crash();original=store._read()
        valid={'tool':'read_file','call_id':'read','ok':True,'path':'a.txt','truncated':True,
            'next_offset':1,'next_char_offset':2,'version':'observed'}
        for field,value in [('path',['a.txt']),('path','bad\0path'),('ok',1),('truncated',[]),
                ('next_offset',True),('next_char_offset',-1),('version',[]),('tool',{}),('call_id',[])]:
            with self.subTest(field=field,value=value):
                data=copy.deepcopy(original);data['runs'][reference]['evidence']=[{**valid,field:value}]
                wire=json.dumps(data).encode();store.path.write_bytes(wire)
                with self.assertRaises(ValueError):store.recover()
                self.assertEqual(store.path.read_bytes(),wire)
                calls=[]
                async def worker(prompt):calls.append(prompt);return 'must not run'
                result=await store.run(ReadOnlyTaskRunner(worker),'parent','continue',reference)
                self.assertFalse(result['ok']);self.assertFalse(result['started']);self.assertEqual(calls,[])
                self.assertEqual(store.path.read_bytes(),wire)

    async def test_child_recovery_does_not_rebill_goal_or_reset_unknown_session_tokens(self):
        from nailong.core.goal import GoalStore
        from langchain_core.messages import AIMessage
        store,reference=self.crash(native=True,budget=True)
        goals=GoalStore(self.sessions.root/'goals.json',project_root=self.root)
        before=goals.latest();pending=before.pending_cost_rounds['native-child-round']
        expected=before.spent_usd+sum(pending['reservations'].values())
        self.assertGreater(before.spent_usd,0);self.assertTrue(pending['reservations'])
        provider_log=Path(self.tmp.name)/'data'/'provider.calls'
        self.assertEqual(provider_log.read_text().splitlines(),['called','called'])
        factory=self.factory([AIMessage(content='must not call provider')],session_token_limit=100000)
        recovered=factory.goal_store.get(before.id)
        self.assertEqual(recovered.state,'paused');self.assertTrue(recovered.cost_recovery_required)
        self.assertAlmostEqual(recovered.spent_usd,expected);self.assertEqual(recovered.round,1)
        self.assertEqual(store._read()['runs'][reference]['status'],'interrupted')
        tokens=factory.session_token_budget('parent')
        self.assertTrue(tokens.stopped);self.assertEqual(tokens.stop_reason,'usage_unknown')
        charged=tokens.spent
        status=await self.invoke(self.tools(factory)['task_status'],{'resume_reference':reference})
        self.assertTrue(status['ok'],status);self.assertFalse(status['runs'][0]['usage_complete'])
        self.assertEqual(factory.goal_store.costs.recover(),[]);self.assertEqual(store.recover(),[])
        self.assertAlmostEqual(factory.goal_store.get(before.id).spent_usd,expected)
        self.assertEqual(tokens.spent,charged)
        self.assertEqual(provider_log.read_text().splitlines(),['called','called'])

    async def test_status_pagination_and_consumed_chain_survive_a_resumed_stage_crash(self):
        store=ChildContinuations(self.sessions,self.tasks)
        async def partial(prompt):
            return ChildTaskResult('partial',{'input_tokens':1,'output_tokens':1},budget_failure={'reason':'stage'})
        first=await store.run(ReadOnlyTaskRunner(partial),'parent','first')
        store,second=self.crash(resume=first['resume_reference'])
        self.assertEqual(store.recover(),[second])
        self.assertEqual(store._read()['runs'][first['resume_reference']]['resumed_by'],second)
        self.assertFalse((await store.run(ReadOnlyTaskRunner(partial),'parent','duplicate',first['resume_reference']))['ok'])
        third=await store.run(ReadOnlyTaskRunner(partial),'parent','continue recovered',second)
        self.assertTrue(third['ok'],third);self.assertEqual(third['parent_run_id'],second)
        page=store.status('parent',limit=2);self.assertEqual(len(page['runs']),2);self.assertEqual(page['next_offset'],2)
        tail=store.status('parent',offset=page['next_offset'],limit=2)
        self.assertEqual(len(tail['runs']),1);self.assertIsNone(tail['next_offset'])
        references={row['resume_reference'] for row in page['runs']+tail['runs']}
        self.assertEqual(references,{first['resume_reference'],second,third['resume_reference']})

    async def test_parent_pause_during_input_observation_cannot_admit_a_child(self):
        from nailong.core.verification import input_fingerprint
        store=ChildContinuations(self.sessions,self.tasks);calls=[]
        def pause(root):
            value=input_fingerprint(root);self.tasks.set_state('parent',lifecycle='paused');return value
        async def worker(prompt):calls.append(prompt);return 'must not start'
        with patch('nailong.core.child_continuations.input_fingerprint',side_effect=pause):
            result=await store.run(ReadOnlyTaskRunner(worker),'parent','research')
        self.assertFalse(result['ok']);self.assertFalse(result['started']);self.assertEqual(calls,[])
        self.assertFalse(store.path.exists())


    async def test_native_read_past_eof_keeps_nullable_content_position_readable(self):
        from langchain_core.messages import AIMessage
        usage={'input_tokens':100,'output_tokens':10,'total_tokens':110}
        factory=self.factory([AIMessage(content='',tool_calls=[{'name':'read_file',
            'args':{'path':'a.txt','offset':100},'id':'past-eof'}],usage_metadata=usage),
            AIMessage(content='未取得所选行，未完整读取文件。',usage_metadata=usage)])
        result=await self.invoke(self.tools(factory)['task'],{'description':'检查 a.txt 第100行'})
        self.assertTrue(result['ok'],result)
        self.assertIsNone(result['evidence'][0]['content_offset'])
        store=factory.task_runner.continuations
        self.assertEqual(store.recover(),[])
        self.assertTrue(store.status('parent',result['resume_reference'])['ok'])


    async def test_native_bypass_external_read_remains_valid_historical_metadata(self):
        from langchain_core.messages import AIMessage
        external=self.root.parent/'external.txt';external.write_text('authorized external fixture')
        usage={'input_tokens':100,'output_tokens':10,'total_tokens':110}
        factory=self.factory([AIMessage(content='',tool_calls=[{'name':'read_file',
            'args':{'path':str(external)},'id':'external'}],usage_metadata=usage),
            AIMessage(content='已读取授权的外部资料。',usage_metadata=usage)])
        factory.tool_execution_context.permission_mode='bypassPermissions'
        result=await self.invoke(self.tools(factory)['task'],{'description':'读取明确授权的外部资料'})
        self.assertTrue(result['ok'],result)
        self.assertTrue(Path(result['evidence'][0]['path']).is_absolute())
        store=factory.task_runner.continuations
        self.assertEqual(store.recover(),[])
        self.assertTrue(store.status('parent',result['resume_reference'])['ok'])


    async def test_parent_pause_while_child_waits_for_runner_capacity_prevents_worker_start(self):
        store=ChildContinuations(self.sessions,self.tasks);entered=asyncio.Event();release=asyncio.Event();starts=[]
        async def worker(prompt):
            starts.append((prompt,self.tasks.snapshot('parent')['lifecycle']))
            if prompt=='first':entered.set();await release.wait()
            return ChildTaskResult('stage',{'input_tokens':1,'output_tokens':1})
        runner=ReadOnlyTaskRunner(worker,max_concurrency=1)
        first=asyncio.create_task(store.run(runner,'parent','first'));second=None
        try:
            await asyncio.wait_for(entered.wait(),2)
            second=asyncio.create_task(store.run(runner,'parent','second'))
            deadline=time.monotonic()+2
            while len(store._read()['runs'])<2 and time.monotonic()<deadline:await asyncio.sleep(.005)
            self.assertEqual(len(store._read()['runs']),2)
            self.tasks.set_state('parent',lifecycle='paused');release.set()
            _,result=await asyncio.gather(first,second)
            self.assertFalse(result['ok'],result)
            self.assertEqual(starts,[('first','active')])
            self.assertEqual(runner._budget('parent').reserved,0)
            row=next(row for row in store._read()['runs'].values() if row['prompt']=='second')
            self.assertEqual(row['status'],'interrupted');self.assertTrue(row['usage_complete'])
            self.assertEqual(row['usage']['total_tokens'],0,'the queued worker never contacted a provider')
        finally:
            release.set()
            await asyncio.gather(first,*([second] if second is not None else []),return_exceptions=True)


    async def test_ordinary_parent_progress_does_not_cancel_an_authorized_queued_child(self):
        store=ChildContinuations(self.sessions,self.tasks);entered=asyncio.Event();release=asyncio.Event();starts=[]
        async def worker(prompt):
            starts.append(prompt)
            if prompt=='first':entered.set();await release.wait()
            return ChildTaskResult('stage',{'input_tokens':1,'output_tokens':1})
        runner=ReadOnlyTaskRunner(worker,max_concurrency=1)
        first=asyncio.create_task(store.run(runner,'parent','first'));second=None
        try:
            await asyncio.wait_for(entered.wait(),2)
            second=asyncio.create_task(store.run(runner,'parent','second'))
            deadline=time.monotonic()+2
            while len(store._read()['runs'])<2 and time.monotonic()<deadline:await asyncio.sleep(.005)
            self.assertEqual(len(store._read()['runs']),2)
            self.tasks.set_state('parent',progress='first child made progress');release.set()
            results=await asyncio.gather(first,second)
            self.assertTrue(all(result['ok'] for result in results),results)
            self.assertEqual(starts,['first','second'])
        finally:
            release.set()
            await asyncio.gather(first,*([second] if second is not None else []),return_exceptions=True)


    async def test_parallel_native_children_keep_separate_progress_receipts(self):
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration,ChatResult
        (self.root/'b.txt').write_text('second fixture')
        factory=self.factory([AIMessage(content='unused')]);both=asyncio.Event();seen=[]
        usage={'input_tokens':100,'output_tokens':10,'total_tokens':110}
        async def generate(messages,*args,**kwargs):
            prompt=next(str(message.content) for message in messages if getattr(message,'type',None)=='human')
            path='a.txt' if 'alpha' in prompt else 'b.txt'
            if any(getattr(message,'type',None)=='tool' for message in messages):
                seen.append(path)
                if len(seen)==2:both.set()
                await asyncio.wait_for(both.wait(),3)
                answer=AIMessage(content='已读取本次指定文件。',usage_metadata=usage)
            else:
                answer=AIMessage(content='',tool_calls=[{'name':'read_file','args':{'path':path},
                    'id':'read-'+path}],usage_metadata=usage)
            return ChatResult(generations=[ChatGeneration(message=answer)])
        tool=self.tools(factory)['task']
        with patch.object(type(factory.model),'_agenerate',side_effect=generate):
            alpha,beta=await asyncio.gather(self.invoke(tool,{'description':'alpha a.txt'}),
                self.invoke(tool,{'description':'beta b.txt'}))
        self.assertTrue(alpha['ok'],alpha);self.assertTrue(beta['ok'],beta)
        self.assertEqual([row['path'] for row in alpha['evidence']],['a.txt'])
        self.assertEqual([row['path'] for row in beta['evidence']],['b.txt'])
        self.assertNotEqual(alpha['resume_reference'],beta['resume_reference'])
        rows=factory.task_runner.continuations._read()['runs']
        self.assertEqual(len(rows[alpha['resume_reference']]['evidence']),1)
        self.assertEqual(len(rows[beta['resume_reference']]['evidence']),1)


if __name__=='__main__':unittest.main()
