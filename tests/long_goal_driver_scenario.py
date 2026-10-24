"""Offline end-to-end acceptance through the shared autonomous goal driver."""
import json
import math
import sys
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import HumanMessage
from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.costs import CostEstimator
from nailong.core.sessions import ProjectSessionStore
from test_context_runtime import RecordingModel
from long_task_scenario import answer
from platform_fixtures import shell_join
from ui.flows import stream_goal

THREAD='long-autonomous'
REQUIREMENT='Preserve every original requirement and acceptance. '*800+'AUTONOMOUS_ORIGINAL_TAIL'


async def run_goal_driver_scenario(base):
    root=base/'project';root.mkdir();(root/'.nailong').mkdir()
    home=base/'home';home.mkdir()
    (root/'source.py').write_text("MODE='bad'\n")
    (root/'notes.txt').write_text(''.join(f'native observation line {i}\n' for i in range(1600))+'AUTONOMOUS_TOOL_TAIL')
    body="from pathlib import Path;ns={};exec(Path('source.py').read_text(),ns);assert Path('notes.txt').read_text().endswith('AUTONOMOUS_TOOL_TAIL');p=Path('bundle');p.mkdir(exist_ok=True);f=p/'partial';f.write_text('failed build output') if ns['MODE']=='bad' else f.unlink(missing_ok=True);(p/'report.csv').write_text('kind,value\\nresult,42\\n');raise SystemExit(1 if ns['MODE']=='bad' else 0)"
    step={'name':'build','kind':'build','command':shell_join([sys.executable,'-B','-c',body]),'generated_paths':['bundle']}
    (root/'.nailong/settings.json').write_text(json.dumps({'verification':{'steps':[step]},
        'budgets':{'session_token_limit':2000000}}))
    settings=Settings('fixture-key','https://offline.invalid','deepseek-flash',root)
    factory=None;all_requests=[];events=[]
    with patch('pathlib.Path.home',return_value=home):
        def reopen():
            model=RecordingModel(responses=[answer()])
            with patch('agent.ChatDeepSeek',return_value=model):
                value=AgentRuntimeFactory(settings,session_store=ProjectSessionStore(root,base_dir=base/'private'))
            return value,AgentService(value),model
        try:
            factory,service,model=reopen();tasks=factory.task_store;goals=factory.goal_store
            task=tasks.begin(THREAD,'修复source.py并验证CSV交付',scope=['source.py','notes.txt'],latest_request=REQUIREMENT)
            tasks.amend(THREAD,constraints=['KEEP_AUTONOMOUS_CSV_HEADER: kind,value'])
            tasks.configure_verification(THREAD,[step]);tasks.add_acceptance(THREAD,'report','CSV的result为42',kind='build')
            tasks.bind_verification(THREAD,'report','build',['source.py','notes.txt'])
            original=tasks.snapshot(THREAD)
            goal=goals.create('修复source.py并验证CSV交付',thread_id=THREAD,max_rounds=20,max_cost_usd=5)
            runtime=await factory.async_runtime(thread_id=THREAD)
            await runtime.aupdate_state({'configurable':{'thread_id':THREAD}},
                {'messages':[HumanMessage(content=REQUIREMENT)]})
            model.responses=[]
            for i in range(7):
                model.responses.extend([answer('',[{'name':'read_file','id':f'observe-{i}',
                    'args':{'path':'notes.txt','offset':1+i*180,'limit':180}}]),
                    answer(f'Observation {i}: '+'recorded native facts. '*200)])
            estimator=CostEstimator(settings.model,root)
            async def collect(current_goal, pause_after=None):
                stream=stream_goal(service,factory,goals,estimator,current_goal,thread_id=THREAD,
                    approval=lambda *a:'approve_once')
                try:
                    async for event in stream:
                        events.append(event)
                        assert event.kind not in {'error','provider_error'},event.data
                        if event.kind=='tool_end':assert event.data.get('ok') is not False,event.data
                        if event.kind=='usage':
                            current=tasks.snapshot(THREAD)
                            request=model.requests[-1]
                            if current['lifecycle']!='completed' or any(m.additional_kwargs.get('nailong_task_context') for m in request):
                                actual='\n'.join(str(m.content) for m in request)
                                assert REQUIREMENT in actual,'active original requirement omitted'
                                assert 'KEEP_AUTONOMOUS_CSV_HEADER' in actual
                            projection=next((m for m in request if m.additional_kwargs.get('nailong_task_context')),None)
                            if projection is not None:
                                info=factory.context_reports[THREAD]['task_projection']
                                assert info['characters']==len(projection.content),(info,len(projection.content))
                            assert current['task_id']==original['task_id']
                            assert current['scope']==original['scope']
                            assert current['constraints']==original['constraints']
                        if event.kind=='goal_progress' and pause_after is not None and goals.get(goal.id).round==pause_after:
                            break
                finally:await stream.aclose()
            await collect(goal,pause_after=7)
            before=goals.get(goal.id)
            assert (before.state,before.round)==('paused',7),before
            assert len(before.settled_round_ids)==7
            assert before.spent_usd>0
            failed=[row['data'] for row in factory.session_store.read_events(THREAD) if row['kind']=='verification']
            assert len(failed)==7 and all(row['status']=='failed' and row['steps'][0]['exit_code']==1 for row in failed),failed
            assert not before.verification_succeeded
            assert all(row['status']=='failed' for row in tasks.snapshot(THREAD)['acceptance'])
            budget=factory.session_token_budget(THREAD)
            spent_tokens=budget.spent
            assert budget.reserved==0
            compacted=await service.compact_context(THREAD)
            assert compacted['compacted'],compacted
            assert any('AUTONOMOUS_ORIGINAL_TAIL' in json.dumps(factory.history_archive.load(THREAD,p.stem))
                for p in factory.history_archive.root.rglob('hist_*.json'))
            all_requests.extend(model.requests)
            await factory.aclose();factory.close()
            factory,service,model=reopen();tasks=factory.task_store;goals=factory.goal_store
            recovered=goals.get(goal.id)
            assert (recovered.id,recovered.round,recovered.spent_usd,recovered.settled_round_ids)==(
                before.id,before.round,before.spent_usd,before.settled_round_ids)
            assert tasks.snapshot(THREAD)['requirements']==original['requirements']
            budget=factory.session_token_budget(THREAD)
            assert budget.spent==spent_tokens and budget.reserved==0
            okay,reason,resumed=goals.resume(goal.id,thread_id=THREAD)
            assert okay,reason
            model.responses=[answer('',[{'name':'read_file','id':'read-repair','args':{'path':'source.py'}}]),
                answer('',[{'name':'write_file','id':'repair','args':{'path':'source.py','content':"MODE='good'\n"}}]),
                answer('Source repaired; core acceptance follows.'),
                answer('',[{'name':'update_goal','id':'complete','args':{'state':'complete','summary':'Native autonomous delivery verified'}}]),
                answer('Verified delivery complete.')]
            await collect(resumed)
            all_requests.extend(model.requests)
            final=goals.get(goal.id)
            assert (final.state,final.round)==('complete',9),final
            assert len(final.settled_round_ids)==9 and len(set(final.settled_round_ids))==9
            assert final.spent_usd>before.spent_usd
            assert math.isclose(final.spent_usd,estimator.estimate({'input_tokens':100*len(all_requests),'output_tokens':10*len(all_requests),'cache_hit_tokens':0}),rel_tol=0,abs_tol=1e-12)
            assert tasks.snapshot(THREAD)['lifecycle']=='completed'
            budget=factory.session_token_budget(THREAD)
            assert budget.reserved==0 and budget.spent==110*len(all_requests),(budget.spent,len(all_requests))
            report=await service.delivery_report(THREAD)
            assert report['status']=='verified',report
            assert not (root/'bundle/partial').exists()
            assert (root/'bundle/report.csv').read_text()=='kind,value\nresult,42\n'
            (root/'bundle/report.csv').write_text('externally changed artifact')
            changed=await service.delivery_report(THREAD)
            assert changed['status']!='verified',changed
            return {'rounds':final.round,'requests':len(all_requests),'spent':final.spent_usd,
                'spent_tokens':budget.spent,'delivery':report['status'],'after_external_change':changed['status'],
                'compacted':compacted['compacted']}
        finally:
            if factory is not None:await factory.aclose();factory.close()
