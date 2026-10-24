"""Real offline runtime scenario shared by long-task integration tests."""
import asyncio,json,shlex,sys,tempfile
from pathlib import Path
from unittest.mock import patch
from langchain_core.messages import AIMessage
from test_context_runtime import RecordingModel
from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.sessions import ProjectSessionStore
from nailong.core.verification import VerificationService

THREAD='long-composite'
ALLOWED={'read_file','write_file','read_history_result','read_task_context','run_command','update_goal'}

def answer(text='阶段已完成。',calls=()):
    return AIMessage(content=text,tool_calls=list(calls),usage_metadata={'input_tokens':100,'output_tokens':10,'total_tokens':110})

async def run_composite_scenario(base, cycles=1):
    root=base/'project';root.mkdir();(root/'.nailong').mkdir();home=base/'home';home.mkdir()
    (root/'source.py').write_text("MODE='bad'\n")
    (root/'notes.txt').write_text('native input evidence\n'*1500+'ORIGINAL_TOOL_TAIL')
    body="from pathlib import Path;ns={};exec(Path('source.py').read_text(),ns);assert all(Path(f'stage-{i}.txt').read_text()==f'native stage {i}' for i in range(1,7));p=Path('bundle');p.mkdir(exist_ok=True);c=p/'count';c.write_text(str(int(c.read_text())+1 if c.exists() else 1));f=p/'partial';f.write_text('known failed output') if ns['MODE']=='bad' else f.unlink(missing_ok=True);(p/'report.csv').write_text('kind,value\\nresult,42\\n');raise SystemExit(1 if ns['MODE']=='bad' else 0)"
    step={'name':'build','kind':'build','command':shlex.join([sys.executable,'-B','-c',body]),'generated_paths':['bundle']}
    (root/'.nailong/settings.json').write_text(json.dumps({'verification':{'steps':[step]}}))
    settings=Settings('offline-key','https://offline.invalid','deepseek-flash',root)
    factory=None
    with patch('pathlib.Path.home',return_value=home):
        def reopen():
            model=RecordingModel(responses=[answer()])
            with patch('agent.ChatDeepSeek',return_value=model):
                value=AgentRuntimeFactory(settings,session_store=ProjectSessionStore(root,base_dir=base/'private'))
            service=AgentService(value);service.compact_threshold_tokens=1000000
            return value,service,model
        try:
            factory,service,model=reopen();tasks=factory.task_store;goals=factory.goal_store
            task=tasks.begin(THREAD,'创建并验证长期报表任务',scope=['source.py',*[f'stage-{i}.txt' for i in range(1,7)]])
            task=tasks.amend(THREAD,constraints=['LONG_TASK_KEEP_HEADER: CSV首行必须是kind,value'])
            tasks.configure_verification(THREAD,[step]);tasks.add_acceptance(THREAD,'report','报表内容正确',kind='build')
            tasks.bind_verification(THREAD,'report','build',['source.py',*[f'stage-{i}.txt' for i in range(1,7)]])
            original=tasks.snapshot(THREAD);goal=goals.create('创建并验证长期报表任务',thread_id=THREAD,max_rounds=50,max_cost_usd=5)
            config={'configurable':{'thread_id':THREAD}}
            async def turn(text,calls,followup=()):
                request_offset=len(model.requests)
                model.i=0;model.responses=[answer('',calls),*([answer('',followup)] if followup else []),answer()]
                events=[e async for e in service.stream_turn(text,config,allowed_tools=ALLOWED,approval_handler=lambda *a:'approve_once')]
                errors=[e.data for e in events if e.kind in {'error','provider_error'}]
                for event in events:
                    if event.kind=='tool_end' and event.data.get('name')=='update_goal':print('COMPLETION_TOOL '+json.dumps(event.data,ensure_ascii=False),flush=True)
                assert not errors,errors
                failures=[e.data for e in events if e.kind=='tool_end' and e.data.get('ok') is False]
                assert not failures,failures
                requests=model.requests[request_offset:]
                assert requests,'provider was not called for the scripted productive turn'
                current=factory.task_store.snapshot(THREAD)
                for request in requests:
                    # After native completion the closing answer can omit the
                    # closed task projection. Every active request keeps it.
                    if current['lifecycle']=='completed' and not any(message.additional_kwargs.get('nailong_task_context') for message in request):continue
                    actual='\n'.join(str(message.content) for message in request)
                    for requirement in current['requirements']:
                        if requirement['status']=='active':assert requirement['text'] in actual,(text[:24],requirement['id'])
                print(json.dumps({'turn':text[:30],'events':[e.kind for e in events],'goal_round':factory.goal_store.get(goal.id).round}),flush=True)
                return events
            compactions=[];original_requirement=None
            for cycle in range(cycles):
                for index in range(7):
                    text=f'按照原任务继续阶段{cycle}-{index}'
                    if cycle==0 and index==0:
                        text+='。最初完整要求：'+'保持所有验收和范围。'*3500+'ORIGINAL_REQUIREMENT_TAIL'
                        original_requirement=text
                    call=({'name':'read_file','args':{'path':'notes.txt'},'id':'read-original'} if index==0 else
                        {'name':'write_file','args':{'path':f'stage-{index}.txt','content':f'native stage {index}'},'id':f'write-{index}'}) if cycle==0 else {
                        'name':'read_file','args':{'path':'notes.txt','offset':1+index*150,'limit':150},'id':f'read-{cycle}-{index}'}
                    await turn(text,[call])
                    current=tasks.snapshot(THREAD)
                    assert current['task_id']==original['task_id'],current['task_id']
                    assert current['constraints']==original['constraints'],current['constraints']
                    assert current['scope']==original['scope']
                    assert {row['id'] for row in current['acceptance']}=={row['id'] for row in original['acceptance']}
                    assert any(row['text']==original_requirement and row['status']=='active' for row in current['requirements'])
                compacted=await service.compact_context(THREAD)
                assert compacted['compacted'],compacted
                compactions.append(compacted)
                before=goals.get(goal.id)
                matches=[]
                for path in factory.history_archive.root.rglob('hist_*.json'):
                    data=factory.history_archive.load(THREAD,path.stem)
                    if 'ORIGINAL_REQUIREMENT_TAIL' in json.dumps(data,ensure_ascii=False):matches.append(path.stem)
                assert matches,'original requirement tail not recoverable from full archive'
                await factory.aclose();factory.close();factory,service,model=reopen();tasks=factory.task_store;goals=factory.goal_store
                recovered=goals.get(goal.id)
                assert (recovered.round,recovered.spent_usd)==(before.round,before.spent_usd)
                assert tasks.snapshot(THREAD)['task_id']==original['task_id']
                assert tasks.snapshot(THREAD)['constraints']==original['constraints']
                assert tasks.snapshot(THREAD)['scope']==original['scope']
                assert any(row['text']==original_requirement and row['status']=='active' for row in tasks.snapshot(THREAD)['requirements'])
                assert (recovered.max_rounds,recovered.max_cost_usd)==(50,5.0)
            first=await VerificationService(root,factory.session_store,goals,task_store=tasks).run(THREAD,approval=lambda *a:'approve_once',permission_engine=service.permission_engine)
            assert first['status']=='failed',first['status']
            assert not goals.update(goal.id,state='complete',thread_id=THREAD)[0]
            await turn('继续修复source.py使原构建验收通过',[{'name':'read_file','args':{'path':'source.py'},'id':'read-repair'}],[{'name':'write_file','args':{'path':'source.py','content':"MODE='good'\n"},'id':'repair'}])
            second=await VerificationService(root,factory.session_store,goals,task_store=tasks).run(THREAD,approval=lambda *a:'approve_once',permission_engine=service.permission_engine)
            assert second['status']=='passed',second['status']
            events=await turn('继续',[{'name':'update_goal','args':{'state':'complete','summary':'native long workflow complete'},'id':'complete'}])
            final=goals.get(goal.id);report=await service.delivery_report(THREAD)
            if final.state!='complete':print('PENDING_REPORT '+json.dumps(report,ensure_ascii=False),flush=True)
            assert final.state=='complete',final
            assert tasks.snapshot(THREAD)['lifecycle']=='completed',tasks.snapshot(THREAD)['lifecycle']
            assert report['status']=='verified',report
            assert (root/'bundle/report.csv').read_text()=='kind,value\nresult,42\n'
            (root/'bundle/report.csv').write_text('external artifact change')
            invalid=await service.delivery_report(THREAD)
            assert invalid['status']!='verified',invalid
            return {'rounds':final.round,'spent':final.spent_usd,'compactions':compactions,'delivery':report['status'],'after_external_change':invalid['status']}
        finally:
            if factory is not None:await factory.aclose();factory.close()
