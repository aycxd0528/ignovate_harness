from platform_fixtures import assert_private, editor_command, kill_process_if_alive, process_exists, python_command, shell_join
import asyncio
import json
import os
import shlex
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_service import AgentService
from nailong.core.diagnostics import usage_snapshot
from nailong.core.permissions import PermissionEngine
from nailong.core.sessions import ProjectSessionStore
from nailong.core.verification import VerificationService
from tests.test_workflow_verification import VerificationTests


class ReviewVerificationTests(VerificationTests):
    async def test_exact_schema_and_unknown_fields(self):
        step={'name':'test','kind':'test','command':'true','timeout_seconds':1}
        self.config([step]); self.assertEqual(self.service().list_steps()[0]['timeout_seconds'],1)
        for extra in ({'unexpected':True},{'timeout':1},{'expected_status':200}):
            self.config([{**step,**extra}])
            with self.assertRaises(ValueError): self.service().list_steps()
        self.config([step])
        path=self.root/'.nailong/settings.json'
        path.write_text(json.dumps({'verification':{'steps':[step],'unexpected':True}}), newline='\n')
        with self.assertRaises(ValueError): self.service().list_steps()

    async def test_generated_directory_does_not_hide_later_input(self):
        self.config([{'name':'build','kind':'build','command':self.command("import pathlib; pathlib.Path('build').mkdir(exist_ok=True); pathlib.Path('build/out').write_text('output')"),'generated_paths':['build']}])
        goal=self.goals.create('verify',thread_id='thread')
        self.assertEqual((await self.run_steps())['status'],'passed')
        (self.root/'build/new_input.py').write_text('source', newline='\n')
        self.assertFalse(self.goals.update(goal.id,state='complete',thread_id='thread')[0])
        with self.assertRaises(ValueError): await self.run_steps()

    async def test_process_does_not_inherit_model_credential(self):
        self.config([{'name':'env','kind':'test','command':self.command("import os; print(os.getenv('DEEPSEEK_API_KEY','absent'))")}])
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-only-sentinel'}): result=await self.run_steps()
        self.assertNotIn('test-only-sentinel',result['steps'][0]['output'])

    async def test_repeated_cancel_cleans_process_and_preserves_step(self):
        self.config([{'name':'cancel','kind':'test','command':self.command("import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); open('pid','w').write(str(os.getpid())); print('started'); time.sleep(30)")}])
        task=asyncio.create_task(self.run_steps())
        while not (self.root/'pid').exists(): await asyncio.sleep(.01)
        pid=int((self.root/'pid').read_text())
        try:
            task.cancel(); await asyncio.sleep(.05); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertFalse(process_exists(pid))
            record=self.store.read_events('thread')[-1]['data']
            self.assertEqual(record['status'],'cancelled'); self.assertEqual(record['steps'][0]['name'],'cancel')
            self.assertIsNotNone(record['steps'][0]['exit_code'])
        finally:
            kill_process_if_alive(pid)


class ReviewAccountingTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_main_stream_is_not_complete_zero_cost(self):
        with tempfile.TemporaryDirectory() as root,tempfile.TemporaryDirectory() as data:
            started=asyncio.Event()
            class Agent:
                async def astream(self,*args,**kwargs):
                    started.set(); await asyncio.sleep(30)
                    yield None
                def get_state(self,*args): return SimpleNamespace(metadata={})
            store=ProjectSessionStore(root,base_dir=data)
            service=AgentService(lambda **kwargs:Agent(),session_store=store,permission_engine=PermissionEngine(root))
            async def run():
                async for event in service.stream_turn('hello',{'configurable':{'thread_id':'thread'}}): pass
            task=asyncio.create_task(run()); await started.wait(); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            snapshot=usage_snapshot(store,'thread')
            self.assertFalse(snapshot['usage_complete']); self.assertIsNone(snapshot['cost_usd'])

    async def test_corrupt_session_records_do_not_break_listing(self):
        with tempfile.TemporaryDirectory() as root,tempfile.TemporaryDirectory() as data:
            store=ProjectSessionStore(root,base_dir=data)
            store.append_event('one','final',{'text':'good'})
            with store.session_path('one').open('a') as stream: stream.write('[1,2]\n'+json.dumps({'kind':'final','data':None})+'\n')
            self.assertEqual(store.list_sessions()[0]['summary'],'good')


class ReviewToolCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_shell_tool_cancellation_stops_side_effects(self):
        from tools import build_tools
        from nailong.tools.files import FileSession
        from nailong.tools.execution import ToolExecutionContext
        with tempfile.TemporaryDirectory() as root:
            execution = ToolExecutionContext(root, approval_handler=lambda *args: 'approve_once')
            tools=build_tools(file_session=FileSession(root), execution_context=execution)
            command=python_command("import time,os; open('pid','w').write(str(os.getpid())); time.sleep(.4); open('late','w').write('bad')")
            tool=next(item for item in tools if item.name=='run_command')
            task=asyncio.create_task(tool.ainvoke({'command':command}))
            async def wait_started():
                while not (Path(root)/'pid').exists():
                    if task.done():
                        self.fail(f'Command finished before starting: {await task}')
                    await asyncio.sleep(.01)
            try:
                await asyncio.wait_for(wait_started(), timeout=3)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(.5)
            self.assertFalse((Path(root)/'late').exists())
