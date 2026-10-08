"""End-state regressions found while auditing the approved workflow design."""
from platform_fixtures import assert_private, editor_command, kill_process_if_alive, process_exists, python_command, shell_join
import asyncio
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import Settings
from nailong.core.diagnostics import usage_snapshot
from nailong.core.goal import GoalStore
from nailong.core.sessions import ProjectSessionStore
from ui.actions import CommandActions
from ui.controller import CommandController
from ui.presentation import SessionMetrics, configured_context_window, render_composer_metrics


class FinalMetricsTests(unittest.TestCase):
    def test_tool_summary_keeps_boolean_exit_code_and_structured_preview(self):
        from agent_service import AgentService
        from langchain_core.messages import ToolMessage
        from nailong.core.permissions import PermissionEngine
        with tempfile.TemporaryDirectory() as directory:
            service=AgentService(lambda **kwargs:None,api_key='private-key',permission_engine=PermissionEngine(directory))
            result=service._tool_result_summary(ToolMessage(content=json.dumps({'ok':False,'exit_code':7,'error':'private-key failed'}),tool_call_id='one',name='run_command'))
            self.assertIs(result['ok'],False);self.assertEqual(result['exit_code'],7)
            self.assertNotIn('private-key',json.dumps(result))
            event=service._tool_start_data('read_file',{'path':'file.py'})
            self.assertIsInstance(event['preview'],dict)

    def test_missing_or_estimated_usage_remains_unknown_in_composer(self):
        metrics=SessionMetrics.from_events([
            {'kind':'usage','data':{'input_tokens':100,'output_tokens':20}},
            {'kind':'usage_missing','data':{'scope':'main'}},
        ])
        self.assertFalse(metrics.usage_complete)
        self.assertIsNone(metrics.last_input_tokens)
        self.assertIn('用量不完整',render_composer_metrics(metrics,model='model',context_window=1000,width=72,compact=True).plain)
        metrics=SessionMetrics.from_events([{'kind':'usage','data':{'input_tokens':25,'output_tokens':0,'estimated':True}}])
        self.assertFalse(metrics.usage_complete)
        self.assertIsNone(metrics.last_input_tokens)

    def test_ui_and_service_use_the_same_configured_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'.nailong').mkdir()
            (root/'.nailong/settings.json').write_text(json.dumps({'context_windows':{'private':4096}}), newline='\n')
            self.assertEqual(configured_context_window('private',root),4096)

    def test_damaged_usage_cannot_be_claimed_as_complete(self):
        with tempfile.TemporaryDirectory() as project,tempfile.TemporaryDirectory() as data:
            store=ProjectSessionStore(project,base_dir=data)
            for value in (True,-1,float('inf'),'bad'):
                store.append_event('thread','usage',{'input_tokens':value,'output_tokens':2})
            report=usage_snapshot(store,'thread')
            self.assertFalse(report['usage_complete'])
            self.assertIsNone(report['cost_usd'])

    def test_doctor_without_site_packages_still_produces_json(self):
        with tempfile.TemporaryDirectory() as directory:
            result=subprocess.run([sys.executable,'-S','main.py','doctor','--project',directory,'--output-format','json'],
                env={**os.environ,'DEEPSEEK_API_KEY':'','DEEPSEEK_BASE_URL':'','DEEPSEEK_MODEL':'','IGNOVATE_CONFIG_DIR':str(Path(directory)/'user')},
                capture_output=True,text=True,timeout=8)
            self.assertEqual(result.returncode,1)
            report=json.loads(result.stdout)
            self.assertFalse(report['network_checked'])
            self.assertTrue(any(row['name']=='langgraph' and row['status']=='error' for row in report['checks']))


    def test_doctor_without_dependencies_reads_user_connection_without_crashing(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            connection=root/'user'
            connection.mkdir()
            (connection/'config.json').write_text(json.dumps({'version':1,'onboarding_complete':True,
                'provider':{'api_base':'https://api.invalid','model':'deepseek-flash','api_key':'fixture-hidden'},
                'reasoning_effort':'low'}), newline='\n')
            result=subprocess.run([sys.executable,'-S','main.py','doctor','--project',directory,'--output-format','json'],
                env={**os.environ,'IGNOVATE_CONFIG_DIR':str(connection)},capture_output=True,text=True,timeout=8)
            self.assertTrue(result.stdout.startswith('{'), 'doctor must emit JSON even with saved user configuration')
            report=json.loads(result.stdout)
            credentials=next(row for row in report['checks'] if row['name']=='credentials')
            self.assertEqual(credentials['status'],'ok')
            self.assertNotIn('fixture-hidden',result.stdout+result.stderr)


class FinalLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_textual_memory_edit_approval_and_skill_menu_refresh(self):
        from agent import AgentRuntimeFactory
        from agent_service import AgentService
        from tui import TerminalAgentApp,PlanEditScreen,ChatInput
        from textual.widgets import TextArea,Static
        with tempfile.TemporaryDirectory() as project,tempfile.TemporaryDirectory() as data:
            root=Path(project).resolve();(root/'.nailong').mkdir()
            path=root/'.nailong/context.md';path.write_text('original', newline='\n')
            settings=Settings('key','https://api.invalid','deepseek-flash',root)
            factory=AgentRuntimeFactory(settings,session_store=ProjectSessionStore(root,base_dir=data))
            app=TerminalAgentApp(AgentService(factory,session_store=factory.session_store),settings)
            async with app.run_test(size=(80,24)) as pilot:
                app._dispatch('/memory edit project');await pilot.pause()
                self.assertIsInstance(app._interaction_panel,PlanEditScreen)
                self.assertIn('记忆',str(app.screen.query_one('#plan-edit-title',Static).render()))
                app.screen.query_one('#plan-edit-content',TextArea).text='updated'
                await pilot.click('#plan-edit-save');await pilot.pause()
                self.assertTrue(app.query_one('#approval-panel').display)
                self.assertEqual(len(app.screen_stack),1)
                self.assertEqual(path.read_text(),'original')
                await pilot.press('escape');await pilot.pause();await app.session_runner.wait_idle()
                self.assertEqual(path.read_text(),'original')
                app._dispatch('/memory edit project');await pilot.pause()
                app.screen.query_one('#plan-edit-content',TextArea).text='approved'
                await pilot.click('#plan-edit-save');await pilot.pause();await pilot.press('2')
                await pilot.pause();await app.session_runner.wait_idle()
                self.assertEqual(path.read_text(),'approved')
                self.assertIn('approved',factory._project_memory[0].content)
                skill=root/'.agents/skills/probe/SKILL.md';skill.parent.mkdir(parents=True)
                skill.write_text('---\nname: probe\ndescription: Menu refresh probe.\n---\nbody', newline='\n')
                app._dispatch('/reload-skills');await pilot.pause();await app.session_runner.wait_idle()
                app.query_one('#composer',ChatInput).text='$pro';await pilot.pause()
                self.assertIn('$probe',str(app.query_one('#command-menu',Static).render()))
                app.query_one('#composer',ChatInput).text=''
                app._dispatch('/skills disable probe');await pilot.pause();await app.session_runner.wait_idle()
                self.assertNotIn('$probe',{spec.name for spec in app.controller.specs()})

    async def test_real_inline_ctrl_c_is_an_input_event_not_loop_interrupt(self):
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        from ui.prompt import build_session
        from ui.input_broker import InputBroker,InputInterrupted
        with tempfile.TemporaryDirectory() as directory,create_pipe_input() as pipe:
            session=build_session(directory,{},input=pipe,output=DummyOutput(),history_path=Path(directory)/'history')
            task=asyncio.create_task(InputBroker(session).prompt_async('> '))
            await asyncio.sleep(.02);pipe.send_text('\x03')
            with self.assertRaises(InputInterrupted):await asyncio.wait_for(task,2)

    async def test_narrow_textual_transcript_has_no_horizontal_scroll_for_normal_answer(self):
        from tui import TerminalAgentApp
        from textual.widgets import RichLog
        with tempfile.TemporaryDirectory() as directory:
            settings=Settings('key','https://api.invalid','deepseek-flash',Path(directory))
            app=TerminalAgentApp(SimpleNamespace(runtime_factory=None,session_store=None),settings)
            async with app.run_test(size=(80,24)) as pilot:
                app._append_user('检查改动')
                app._append_assistant('## 检查结果\n\n短回答与 `file.py:2`。\n\n- 尚未验证')
                await pilot.pause()
                log=app.query_one('#transcript',RichLog)
                self.assertEqual(log.max_scroll_x,0)

    async def test_cancelled_mutating_command_invalidates_prior_goal_proof(self):
        from tools import build_tools
        from nailong.tools.files import FileSession
        from nailong.tools.execution import ToolExecutionContext
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();goals=GoalStore(root/'data/goals.json')
            goal=goals.create('work',thread_id='thread')
            approvals=[]
            def approve(action, permission):
                approvals.append(action['args']['command'])
                return 'approve_once'
            execution=ToolExecutionContext(root,approval_handler=approve)
            tool=next(item for item in build_tools(file_session=FileSession(root),goal_store=goals,
                thread_id='thread',execution_context=execution) if item.name=='run_command')
            proof=json.loads(await tool.ainvoke({'command':python_command('pass')}))
            self.assertTrue(proof['ok']);self.assertTrue(proof['started'])
            self.assertTrue(goals.get(goal.id).verification_succeeded)
            command=shell_join([sys.executable,'-B','-c',"from pathlib import Path;import os,time;Path('pid').write_text(str(os.getpid()));Path('side-effect').write_text('changed');time.sleep(30);Path('late').write_text('unexpected')"])
            task=asyncio.create_task(tool.ainvoke({'command':command}))
            try:
                async def started():
                    while not (root/'side-effect').exists():
                        if task.done():
                            self.fail('命令在创建标记前已结束：'+str(await task))
                        await asyncio.sleep(.01)
                await asyncio.wait_for(started(),3)
                self.assertTrue((root/'side-effect').exists())
                pid=int((root/'pid').read_text())
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
            self.assertEqual(approvals,[python_command('pass'),command])
            self.assertFalse((root/'late').exists())
            self.assertFalse(process_exists(pid))
            self.assertFalse(goals.get(goal.id).verification_succeeded)
            self.assertFalse(goals.update(goal.id,state='complete',thread_id='thread')[0])

    async def test_recap_marks_external_changes_as_unverified(self):
        from nailong.core.verification import VerificationService
        from nailong.core.session_actions import SessionActions
        with tempfile.TemporaryDirectory() as directory,tempfile.TemporaryDirectory() as data:
            root=Path(directory).resolve();(root/'.nailong').mkdir()
            (root/'input.py').write_text('original', newline='\n')
            (root/'.nailong/settings.json').write_text(json.dumps({'verification':{'steps':[{'name':'test','kind':'test','command':python_command('pass')}]}}), newline='\n')
            store=ProjectSessionStore(root,base_dir=data)
            async def approve(*args): return 'approve'
            result=await VerificationService(root,store).run('thread',approval=approve)
            self.assertEqual(result['status'],'passed')
            actions=SessionActions(root,store)
            self.assertIn('已验证：完整项目验证通过',actions.recap('thread'))
            (root/'input.py').write_text('changed', newline='\n')
            self.assertIn('未验证：',actions.recap('thread'))

    async def test_stop_pauses_the_current_session_goal(self):
        from nailong.core.runner import SessionRunner
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);goals=GoalStore(root/'goals.json')
            goal=goals.create('work',thread_id='thread')
            settings=Settings('key','https://api.invalid','deepseek-flash',root)
            controller=CommandController(settings);runner=SessionRunner(root,'thread')
            service=SimpleNamespace(runtime_factory=SimpleNamespace(settings=settings,goal_store=goals))
            await CommandActions(controller).execute(controller.resolve('/stop'),service,'thread',runner=runner)
            self.assertEqual(goals.get(goal.id).state,'paused')
            await runner.close()

    async def test_environment_default_survives_switching_model_and_project(self):
        from agent import AgentRuntimeFactory
        with tempfile.TemporaryDirectory() as directory,tempfile.TemporaryDirectory() as other,tempfile.TemporaryDirectory() as data:
            root=Path(directory);(root/'.nailong').mkdir()
            (root/'.nailong/settings.json').write_text(json.dumps({'models':{'pro':{'model':'deepseek-v4-pro'}},'model':'pro'}), newline='\n')
            settings=Settings('key','https://api.invalid','deepseek-flash',root)
            factory=AgentRuntimeFactory(settings,session_store=ProjectSessionStore(root,base_dir=data))
            self.addAsyncCleanup(factory.aclose);self.addCleanup(factory.close)
            self.assertEqual(factory.settings.model,'deepseek-v4-pro')
            factory.set_model('deepseek-v4-pro')
            next_factory=AgentRuntimeFactory(replace(factory.settings,project_root=Path(other)),
                session_store=ProjectSessionStore(other,base_dir=data))
            self.addAsyncCleanup(next_factory.aclose);self.addCleanup(next_factory.close)
            self.assertEqual(next_factory.preferences.effective()['models']['default']['model'],'deepseek-flash')

    async def test_editor_private_copy_is_removed_and_process_stopped_on_cancel(self):
        from nailong.core.plan import edit_plan_with_editor_async
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);script=root/'editor.py';info=root/'info.json'
            script.write_text("import json,os,sys,time\nfrom pathlib import Path\np=Path(sys.argv[1])\nPath("+repr(str(info))+").write_text(json.dumps({'pid':os.getpid(),'path':str(p),'mode':p.stat().st_mode&0o777,'key':os.environ.get('DEEPSEEK_API_KEY')}))\ntime.sleep(30)\n", newline='\n')
            with patch.dict(os.environ,{'VISUAL':editor_command([sys.executable,str(script)]),'DEEPSEEK_API_KEY':'unit-private-key'}):
                task=asyncio.create_task(edit_plan_with_editor_async('original'))
                try:
                    for _ in range(100):
                        if info.exists(): break
                        await asyncio.sleep(.01)
                    self.assertTrue(info.exists())
                    row=json.loads(info.read_text());assert_private(self, Path(row['path']));self.assertIsNone(row['key'])
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError): await task
            self.assertFalse(Path(row['path']).exists())
            self.assertFalse(process_exists(row['pid']))

    async def test_child_prices_are_per_call_including_zero_token_records(self):
        from nailong.tools.agents import ChildTaskResult,ReadOnlyTaskRunner
        with tempfile.TemporaryDirectory() as project,tempfile.TemporaryDirectory() as data:
            def row(tokens,price):
                return {'input_tokens':tokens,'output_tokens':0,'cache_hit_tokens':0,'model':f'model-{price}',
                    'price_snapshot':{'input_per_million':price,'cache_hit_per_million':0,'output_per_million':0}}
            calls=[row(1_000_000,1),row(1_000_000,2),row(0,3)]
            async def worker(prompt):
                return ChildTaskResult('done',{'input_tokens':2_000_000,'output_tokens':0},True,calls)
            runner=ReadOnlyTaskRunner(worker,token_budget=3_000_000)
            await runner.run('thread','work');usage=runner.drain_usage('thread')
            store=ProjectSessionStore(project,base_dir=data);store.append_event('thread','usage',usage)
            snapshot=usage_snapshot(store,'thread')
            self.assertEqual(snapshot['calls'],3);self.assertEqual(snapshot['cost_usd'],3)
            async def zero(prompt): return ChildTaskResult('done',{},True,[row(0,3)])
            zero_runner=ReadOnlyTaskRunner(zero);await zero_runner.run('zero','work')
            self.assertEqual(len(zero_runner.drain_usage('zero')['calls']),1)

    async def test_textual_rename_and_tool_details_are_visible(self):
        from tui import TerminalAgentApp
        from textual.widgets import Static,RichLog
        from rich.text import Text
        from agent_service import TurnEvent
        with tempfile.TemporaryDirectory() as project,tempfile.TemporaryDirectory() as data:
            store=ProjectSessionStore(project,base_dir=data)
            settings=Settings('key','https://api.invalid','deepseek-flash',Path(project))
            app=TerminalAgentApp(SimpleNamespace(runtime_factory=None,session_store=store),settings)
            async with app.run_test(size=(80,24)) as pilot:
                await app._perform_request(app.controller.resolve('/rename 审查会话'))
                self.assertIn('审查会话',str(app.query_one('#session-info',Static).render()))
                event=TurnEvent('tool_end',{'name':'read_file','summary':'file.py','ok':True,'elapsed_ms':2,
                    'output_snippet':'safe detail','args':{'hidden':'raw-private-arguments'}})
                app._append_activity(event,[Text(app._ui_theme.bullet+' read_file · file.py')]);app._flush_tool_rows()
                await pilot.pause()
                log=app.query_one('#transcript',RichLog)
                self.assertTrue(any(segment.style and segment.style.meta.get('nailong_group')
                    for line in log.lines for segment in line))
                await pilot.press('ctrl+o')
                await pilot.pause()
                conversation = app.screen
                # RichLog preserves clickable metadata on the actual rendered line.
                clicked=False
                for y in range(log.size.height):
                    line=log.render_line(y)
                    if any(segment.style and segment.style.meta.get('nailong_tool') for segment in line):
                        from rich.cells import cell_len
                        prefix=0
                        for segment in line:
                            if segment.style and segment.style.meta.get('nailong_tool'): break
                            prefix+=cell_len(segment.text)
                        x=log.content_region.x-log.region.x+prefix+1
                        actual_y=log.content_region.y-log.region.y+y
                        await pilot.click('#transcript',offset=(x,actual_y));clicked=True;break
                self.assertTrue(clicked,'tool row is not visible/clickable')
                await pilot.pause();self.assertIs(app.screen,conversation)
                detail='\n'.join(line.text for line in log.lines)
                self.assertIn('safe detail',detail);self.assertNotIn('raw-private-arguments',detail)
                self.assertFalse(app.query('#tool-details-dialog'))
