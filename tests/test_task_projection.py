"""Task projection contracts, using real local stores and offline model calls."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import Settings
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore


class TaskFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'project'; self.root.mkdir()
        self.sessions=ProjectSessionStore(self.root,base_dir=Path(self.temp.name)/'data')
        self.tasks=TaskStore(self.sessions)

    def replace(self,identity,text):
        method=getattr(self.tasks,'replace_requirement',None)
        self.assertTrue(callable(method),'TaskStore must expose explicit user replacement')
        return method('one',identity,text)


class RequirementTests(TaskFixture):
    def test_additions_stay_active_and_continuation_does_not_retire_them(self):
        self.tasks.begin('one','保留已有账户')
        self.tasks.begin('one','增加访问日志')
        task=self.tasks.begin('one','继续')
        self.assertIn('requirements',task)
        self.assertEqual(task['request_history'],['保留已有账户','增加访问日志'])
        self.assertEqual([(r['id'],r['text'],r['status'],r['source'],r['history_index']) for r in task['requirements']],
            [('r000001','保留已有账户','active','user',0),('r000002','增加访问日志','active','user',1)])

    def test_explicit_replacement_preserves_original_and_invalidates_old_evidence(self):
        first=self.tasks.begin('one','采用方案甲')
        self.tasks.add_acceptance('one','feature','检查当前方案',kind='test')
        self.tasks.record_evidence('one',{'id':'old','kind':'test','source':'runtime','status':'passed',
            'task_revision':2,'paths':['.'],'input_fingerprint':'old','coverage':'complete','summary':'旧检查','artifact_ref':'verification:old'})
        before=self.tasks.snapshot('one')
        task=self.replace('r000001','采用方案乙')
        self.assertEqual(task['task_id'],first['task_id'])
        self.assertEqual(task['revision'],before['revision']+1)
        self.assertEqual(task['request_history'],['采用方案甲','采用方案乙'])
        self.assertEqual(task['objective'],'采用方案乙')
        self.assertEqual(task['requirements'][0]['superseded_by'],'r000002')
        self.assertEqual(task['requirements'][0]['status'],'superseded')
        self.assertEqual(task['requirements'][1]['status'],'active')
        self.assertEqual(task['evidence'],before['evidence'])
        restored=TaskStore(self.sessions).snapshot('one')
        self.assertEqual(restored,task)
        with self.assertRaises(ValueError): self.replace('r000001','又一次替换')
        self.assertEqual(self.tasks.snapshot('one'),task)

    def test_legacy_history_is_migrated_conservatively_when_appending(self):
        legacy=self.tasks.begin('one','第一条要求')
        legacy.pop('requirements',None); self.tasks._write('one',legacy)
        task=self.tasks.begin('one','第二条要求')
        self.assertIn('requirements',task)
        self.assertEqual([r['status'] for r in task['requirements']],['active','active'])
        self.assertIsNone(task['requirements'][0]['introduced_revision'])
        self.assertEqual(task['requirements'][1]['introduced_revision'],2)

    def test_persisted_mapping_source_and_replacement_chain_are_validated(self):
        self.tasks.begin('one','第一条')
        valid=self.replace('r000001','第二条')
        mutations=[lambda t:t['requirements'][0].update(source='model'),
            lambda t:t['requirements'][0].update(text='改写原文'),
            lambda t:t['requirements'].pop(),
            lambda t:t['requirements'][0].update(superseded_by='r000001'),
            lambda t:t['requirements'][0].update(superseded_by='missing'),
            lambda t:t['requirements'][1].update(introduced_revision=1)]
        for mutate in mutations:
            damaged=copy.deepcopy(valid); mutate(damaged)
            self.tasks._write('one',damaged)
            with self.subTest(damaged=damaged['requirements']):
                with self.assertRaises(ValueError): TaskStore(self.sessions).snapshot('one')
        self.tasks._write('one',valid)


class RequirementCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_can_discover_ids_and_explicitly_replace_from_task_command(self):
        from ui.actions import CommandActions
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'project'; root.mkdir()
            sessions=ProjectSessionStore(root,base_dir=Path(directory)/'data')
            tasks=TaskStore(sessions); tasks.begin('one','旧要求')
            settings=Settings('fake','https://example.invalid','private',root)
            factory=SimpleNamespace(task_store=tasks,settings=settings)
            actions=CommandActions(SimpleNamespace(settings=settings))
            service=SimpleNamespace(runtime_factory=factory,session_store=sessions)
            result=await actions.execute(SimpleNamespace(command='/task',argument='requirements'),service,'one')
            self.assertIn('r000001',result.text)
            result=await actions.execute(SimpleNamespace(command='/task',argument='replace r000001 新要求'),service,'one')
            self.assertEqual(result.data['task']['objective'],'新要求')
            self.assertEqual(tasks.snapshot('one')['requirements'][0]['status'],'superseded')


class TaskHistoryTests(TaskFixture):
    def history(self,thread='one',root=None):
        from nailong.core.history_archive import HistoryArchive
        archive=HistoryArchive(self.sessions.root/'task-test-archive',api_key='fake-secret')
        self.assertTrue(callable(getattr(archive,'load',None)),'Archive must support validated complete loading')
        from nailong.core.task_history import TaskContextHistory
        return TaskContextHistory(archive,thread,root or self.root)

    def test_original_requirements_are_recoverable_by_id_and_keyword(self):
        self.tasks.begin('one','方案甲 fake-secret')
        task=self.replace('r000001','方案乙')
        history=self.history(); ref=history.save(task)
        restored=history.read(ref,section='requirements',record_id='r000001')
        rows=json.loads(restored['content'])
        self.assertEqual(rows[0]['text'],'方案甲 [密钥已隐藏]')
        self.assertEqual(rows[0]['superseded_by'],'r000002')
        self.assertTrue(restored['historical']); self.assertTrue(restored['archive_complete'])
        found=history.read(ref,query='方案甲')
        self.assertIn('r000001',found['content'])
        self.assertNotIn('fake-secret',json.dumps(found,ensure_ascii=False))

    def test_pages_keep_same_version_after_current_task_changes(self):
        self.tasks.begin('one','原始要求'+'文本'*1000)
        history=self.history(); ref=history.save(self.tasks.snapshot('one'))
        pieces=[]; offset=0; versions=[]
        while True:
            page=history.read(ref,section='requirements',offset=offset,max_chars=113)
            pieces.append(page['content']); versions.append(page['version'])
            if not page['truncated']: break
            offset=page['next_offset']
            self.assertLess(len(pieces),100)
            self.tasks.begin('one','新要求')
        self.assertEqual(json.loads(''.join(pieces))[0]['text'],'原始要求'+'文本'*1000)
        self.assertTrue(all(version==versions[0] for version in versions))
        with self.assertRaises(ValueError): history.read(ref,offset=-1)
        with self.assertRaises(ValueError): history.read(ref,section='files')

    def test_cross_thread_project_and_modified_archives_are_rejected(self):
        task=self.tasks.begin('one','保持边界')
        history=self.history(); ref=history.save(task)
        with self.assertRaises(ValueError): self.history('other').read(ref)
        with self.assertRaises(ValueError): self.history(root=self.root/'other').read(ref)
        paths=list(history.archive.root.glob('*/'+ref+'.json'))
        self.assertEqual(len(paths),1)
        record=json.loads(paths[0].read_text())
        record['message']['data']['content']=record['message']['data']['content'].replace('保持边界','改写内容')
        paths[0].write_text(json.dumps(record,ensure_ascii=False))
        with self.assertRaises(ValueError): history.read(ref)

    def test_incomplete_archive_is_not_a_recovery_source(self):
        self.tasks.begin('one','保护原文'+'中'*10000)
        history=self.history()
        with patch('nailong.core.history_archive.MAX_ARCHIVE_BYTES',5000):
            with self.assertRaises(ValueError): history.save(self.tasks.snapshot('one'))


def long_task(root):
    from tests.test_managed_context import snapshot
    task=snapshot(root)
    task['input_fingerprint']='a'*64
    task['steps']=[{'id':f's{i}','title':f'已完成步骤 {i}','state':'done','dependencies':[]} for i in range(30)]
    task['steps'].append({'id':'current','title':'处理当前错误','state':'doing','dependencies':['s29']})
    task['acceptance']=[{'id':f'a{i}','description':f'条件 {i} 的接口必须保持兼容','kind':'test',
        'required':True,'status':'passed','evidence_ids':[f'e{i}']} for i in range(30)]
    task['evidence']=[{'id':f'e{i}','kind':'test','source':'runtime','status':'passed','task_revision':1,
        'paths':[f'src/m{i}.py'],'input_fingerprint':'a'*64,'coverage':'complete',
        'summary':f'先前运行 {i}','artifact_ref':f'verification:run-{i}'} for i in range(30)]
    return task


class ProjectionTests(TaskFixture):
    def history(self):
        from nailong.core.history_archive import HistoryArchive
        from nailong.core.task_history import TaskContextHistory
        return TaskContextHistory(HistoryArchive(self.sessions.root/'archive'),'one',self.root)

    def render(self,task,reference):
        from nailong.core.task_context import render_task_context
        return render_task_context(task,history_reference=reference)

    def test_long_task_keeps_all_acceptance_and_can_restore_omitted_evidence(self):
        from nailong.core.delivery import build_delivery_report
        from nailong.core.task_context import render_task_context, TaskContextTooLargeError
        task=long_task(self.root.resolve()); original=copy.deepcopy(task)
        before=build_delivery_report(task,current_input_fingerprint='a'*64)
        with self.assertRaises(TaskContextTooLargeError): render_task_context(task)
        history=self.history(); reference=history.save(task)
        text=self.render(task,reference)
        self.assertLess(len(text),12000)
        for phrase in ('禁止更改数据库结构','处理当前错误','s29','read_task_context',reference): self.assertIn(phrase,text)
        for i in range(30): self.assertIn(f'条件 {i} 的接口必须保持兼容',text)
        self.assertLess(text.count('verification:run-'),7)
        recovered=history.read(reference,section='evidence',record_id='e0')
        self.assertEqual(json.loads(recovered['content'])[0]['artifact_ref'],'verification:run-0')
        self.assertEqual(task,original)
        self.assertEqual(build_delivery_report(task,current_input_fingerprint='a'*64),before)

    def test_replaced_requirement_leaves_core_but_remains_recoverable(self):
        self.tasks.begin('one','禁止使用方案甲')
        self.tasks.begin('one','保留访问日志')
        task=self.replace('r000001','允许使用方案甲')
        history=self.history(); reference=history.save(task)
        text=self.render(task,reference)
        self.assertIn('允许使用方案甲',text); self.assertIn('保留访问日志',text)
        self.assertNotIn('禁止使用方案甲',text)
        self.assertIn('禁止使用方案甲',history.read(reference,section='requirements',record_id='r000001')['content'])

    def test_real_hard_constraints_still_fail_and_legacy_requirements_stay_active(self):
        from nailong.core.task_context import TaskContextTooLargeError
        task=long_task(self.root.resolve()); task['request_history'] += ['必须保留旧接口']
        task['latest_request']='继续'
        history=self.history(); ref=history.save(task)
        self.assertIn('必须保留旧接口',self.render(task,ref))
        task['constraints']=['硬约束'*5000]
        with self.assertRaises(TaskContextTooLargeError): self.render(task,history.save(task))

    def test_unavailable_reader_falls_back_and_archive_failure_stops_call(self):
        from tests.test_managed_context import OfflineModel
        from langchain.agents.middleware.types import ModelRequest
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
        from nailong.core.context import UsageCalibration
        from nailong.core.context_runtime import ContextManagerMiddleware, ContextLimitExceeded
        task=long_task(self.root.resolve()); history=self.history()
        tool={'type':'function','function':{'name':'read_task_context','description':'restore','parameters':{'type':'object'}}}
        model=OfflineModel(responses=[AIMessage(content='done')])
        manager=ContextManagerMiddleware(Settings('fake','https://example.invalid','private',self.root),[tool],
            'BASE','chat','one',None,None,{},UsageCalibration(),task_snapshot_provider=lambda _:task,task_history=history)
        request=ModelRequest(model=model,messages=[HumanMessage(content='继续')],tools=[tool],system_message=SystemMessage(content='BASE'))
        prepared,_=manager._prepare_request(request)
        self.assertIn('read_task_context',prepared.messages[-1].content)
        with self.assertRaises(ContextLimitExceeded): manager._prepare_request(request.override(tools=[]))
        with patch.object(history,'save',side_effect=OSError('failed')):
            with self.assertRaises(ContextLimitExceeded): manager._prepare_request(request)
        self.assertEqual(len(request.messages),1)


class ProjectionGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_graph_uses_compact_task_and_actual_restore_tool(self):
        from tests.test_managed_context import OfflineModel
        from langchain_core.messages import AIMessage, HumanMessage
        from agent import AgentRuntimeFactory
        from nailong.core.memory import load_project_memory
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'project'; root.mkdir()
            model=OfflineModel(responses=[AIMessage(content='done')])
            settings=Settings('fake','https://example.invalid','private',root)
            with patch('agent.ChatDeepSeek',return_value=model),patch('agent.load_project_memory',side_effect=lambda root,**kwargs:load_project_memory(root,**{**kwargs,'user_file':Path(directory)/'user.md'})):
                factory=AgentRuntimeFactory(settings,session_store=ProjectSessionStore(root,base_dir=Path(directory)/'data'))
                try:
                    task=factory.task_store.begin('one','修复认证')
                    sample=long_task(root.resolve()); sample['task_id']=task['task_id']; sample['requirements']=task['requirements']
                    factory.task_store._write('one',sample)
                    runtime=await factory.async_runtime(thread_id='one',allowed_tools={'read_task_context'})
                    config={'configurable':{'thread_id':'one'}}
                    await runtime.ainvoke({'messages':[HumanMessage(content='继续')]},config,version='v2')
                    self.assertLess(len(model.requests[-1][-1].content),12000)
                    info=factory.context_reports['one']['task_projection']
                    self.assertIn('history_reference',info)
                    tools=factory._build_runtime(thread_id='one',allowed_tools={'read_task_context'})._nailong_tool_specs
                    self.assertIn('read_task_context',tools)
                    state=await runtime.aget_state(config)
                    self.assertFalse(any(m.additional_kwargs.get('nailong_task_context') for m in state.values['messages']))
                finally:
                    await factory.aclose(); factory.close()
