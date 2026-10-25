"""Actual runtime entry points must survive os._exit without dollar resets."""
import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.costs import CostEstimator
from nailong.core.goal import GoalStore
from nailong.core.sessions import ProjectSessionStore


def crash_runtime(directory,entry,window):
    root=Path(directory);project=root/'project';project.mkdir(exist_ok=True)
    home=root/'home';home.mkdir(exist_ok=True)
    if not (project/'source.py').exists():(project/'source.py').write_text('source')
    armed={'value':entry!='coordination'}
    class OfflineModel(FakeMessagesListChatModel):
        def bind_tools(self,tools,**kwargs):return self
        async def _agenerate(self,messages,stop=None,run_manager=None,**kwargs):
            if armed['value']:
                with (root/'provider.calls').open('a') as stream:stream.write('called\n')
                if window=='provider':os._exit(91)
            return await super()._agenerate(messages,stop=stop,run_manager=run_manager,**kwargs)

    async def run():
        settings=Settings('offline-key','https://offline.invalid','deepseek-flash',project)
        factory=AgentRuntimeFactory(settings,session_store=ProjectSessionStore(project,base_dir=root/'data'))
        factory.model=OfflineModel(responses=[AIMessage(content='observed',usage_metadata={
            'input_tokens':1000,'output_tokens':200,'total_tokens':1200})])
        goals=factory.goal_store
        objective='协调已有会话 target 研究 source.py' if entry=='coordination' else 'research'
        goal=goals.latest() or goals.create(objective,thread_id='owner',max_cost_usd=.1,max_rounds=5)
        if factory.task_store.snapshot('owner') is None:
            factory.task_store.begin('owner',objective,scope=['source.py'])
            factory.task_store.add_acceptance('owner','feature','research source',kind='test')
        service=AgentService(factory)
        if entry=='headless':
            import headless
            class Output(io.StringIO):
                def write(self,text):
                    try:value=json.loads(text)
                    except ValueError:value={}
                    if window=='usage' and value.get('type')=='usage':os._exit(91)
                    return super().write(text)
            with patch.object(headless,'create_agent_runtime',return_value=SimpleNamespace(_nailong_runtime_factory=factory)):
                await headless.run_print(settings,'research',goal_mode=True,output_format='stream-json',
                    stdout=Output(),stderr=io.StringIO())
        else:
            if entry=='coordination':
                from nailong.core.session_coordination import SessionCoordination
                coordinator=SessionCoordination(factory,'owner')
                if not coordinator.path.exists():
                    factory.task_store.begin('target','研究 source.py')
                    [event async for event in service.stream_turn('先分析上下文',{'configurable':{'thread_id':'target'}})]
                    lease=coordinator.acquire('target',factory.task_store.snapshot('target')['revision'],['.'])
                    assert lease['ok'],lease
                    assert coordinator.dispatch('native','target','研究 source.py',lease['generation'],[])['ok']
                armed['value']=True
                stream=coordinator.drain(service,approval=lambda *args:'approve_once')
            elif entry=='ordinary':
                stream=service.stream_turn('继续推进 research 目标',{'configurable':{'thread_id':'owner'}})
            else:
                from ui.flows import stream_goal
                stream=stream_goal(service,factory,goals,CostEstimator(settings.model,project),
                    goal,thread_id='owner',approval=lambda *args:'approve_once')
            from nailong.core.budgets import CostBudgetExceeded
            try:
                async for event in stream:
                    if window=='usage' and event.kind=='usage':os._exit(91)
            except CostBudgetExceeded:
                if goals.get(goal.id).state!='paused':raise
        saved=goals.get(goal.id)
        await factory.aclose();factory.close()
        if saved.state=='paused':os._exit(92)
        raise AssertionError('fixture never reached the requested provider/usage window')
    with patch('pathlib.Path.home',return_value=home):asyncio.run(run())


class NativeGoalCostCrashTests(unittest.TestCase):
    def test_all_native_entry_points_preserve_dollars_and_refuse_automatic_replay(self):
        environment={**os.environ,'PYTHONPATH':os.pathsep.join([str(Path(__file__).resolve().parent.parent),
            str(Path(__file__).resolve().parent)])}
        script='from test_goal_cost_runtime_crashes import crash_runtime;import sys;crash_runtime(*sys.argv[1:])'
        for entry in ('ordinary','ui','headless','coordination'):
            for window in ('provider','usage'):
                with self.subTest(entry=entry,window=window),tempfile.TemporaryDirectory() as directory:
                    args=[sys.executable,'-c',script,directory,entry,window]
                    first=subprocess.run(args,env=environment,capture_output=True,timeout=25)
                    self.assertEqual(first.returncode,91,first.stderr.decode())
                    second=subprocess.run(args,env=environment,capture_output=True,timeout=25)
                    self.assertEqual(second.returncode,92,second.stderr.decode())
                    root=Path(directory);sessions=ProjectSessionStore(root/'project',base_dir=root/'data')
                    saved=GoalStore(sessions.root/'goals.json').latest()
                    self.assertEqual(saved.state,'paused');self.assertEqual(saved.round,1)
                    self.assertEqual(saved.pending_cost_rounds,{})
                    self.assertEqual((root/'provider.calls').read_text().splitlines(),['called'])
                    if window=='usage':
                        expected=CostEstimator('deepseek-flash',root/'project').estimate({
                            'input_tokens':1000,'output_tokens':200})
                        self.assertAlmostEqual(saved.spent_usd,expected)
                    else:self.assertGreater(saved.spent_usd,0)
