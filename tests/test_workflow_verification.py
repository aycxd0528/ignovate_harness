import asyncio
import importlib
import json
import shlex
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from nailong.core.permissions import PermissionEngine
from nailong.core.sessions import ProjectSessionStore
from nailong.core.goal import GoalStore

class VerificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name).resolve()
        self.store=ProjectSessionStore(self.root,base_dir=self.root/'data')
        # Evidence storage is outside input project.
        self.data=tempfile.TemporaryDirectory(); self.addCleanup(self.data.cleanup)
        self.store=ProjectSessionStore(self.root,base_dir=self.data.name,api_key='secret-token')
        self.goals=GoalStore(Path(self.data.name)/'goals.json')
        self.engine=PermissionEngine(self.root,rules={})
    def config(self,steps):
        (self.root/'.nailong').mkdir(exist_ok=True)
        (self.root/'.nailong/settings.json').write_text(json.dumps({'verification':{'steps':steps}}))
    def command(self,code): return shlex.quote(sys.executable)+' -B -u -c '+shlex.quote(code)
    def service(self):
        try: cls=importlib.import_module('nailong.core.verification').VerificationService
        except ModuleNotFoundError: self.fail('Verification service missing')
        return cls(self.root,self.store,self.goals,api_key='secret-token')
    async def run_steps(self,name=None,approval='approve_once',mode='default'):
        async def approve(*args): return approval
        return await self.service().run('thread',name,approval=approve,permission_engine=self.engine,permission_mode=mode)
    async def test_success_failure_partial_and_secret_evidence(self):
        self.config([{'name':'test','kind':'test','command':self.command("print('secret-token')")},
                     {'name':'build','kind':'build','command':self.command('raise SystemExit(3)')}])
        goal=self.goals.create('verify',thread_id='thread')
        partial=await self.run_steps('test')
        self.assertEqual(partial['status'],'partial'); self.assertNotIn('secret-token',json.dumps(partial))
        self.assertFalse(self.goals.update(goal.id,state='complete',thread_id='thread')[0])
        full=await self.run_steps()
        self.assertEqual(full['status'],'failed'); self.assertEqual(full['steps'][1]['exit_code'],3)
        self.assertEqual(self.store.read_events('thread')[-1]['kind'],'verification')
    async def test_complete_evidence_invalidated_by_external_edit(self):
        (self.root/'source.py').write_text('before')
        self.config([{'name':'test','kind':'test','command':self.command('print("ok")')}])
        goal=self.goals.create('verify',thread_id='thread')
        result=await self.run_steps(); self.assertEqual(result['status'],'passed')
        self.assertTrue(self.goals.get(goal.id).verification_succeeded)
        (self.root/'source.py').write_text('external change')
        self.assertFalse(self.goals.update(goal.id,state='complete',thread_id='thread')[0])
        result=await self.run_steps(); self.assertEqual(result['status'],'passed')
        self.assertTrue(self.goals.update(goal.id,state='complete',thread_id='thread')[0])

    async def test_executable_permission_change_invalidates_verification(self):
        script = self.root/'check.sh'
        script.write_text('#!/bin/sh\nexit 0\n')
        script.chmod(0o755)
        self.config([{'name':'test', 'kind':'test', 'command':'./check.sh'}])
        goal = self.goals.create('verify', thread_id='thread')
        self.assertEqual((await self.run_steps())['status'], 'passed')
        script.chmod(0o644)
        self.assertFalse(self.goals.update(goal.id, state='complete', thread_id='thread')[0])
    async def test_reject_plan_and_no_approval_execute_nothing(self):
        self.config([{'name':'touch','kind':'test','command':self.command("open('ran','w').write('x')")}])
        for mode,approval in [('default','reject'),('plan','approve_once')]:
            result=await self.run_steps(approval=approval,mode=mode)
            self.assertEqual(result['status'],'unverified'); self.assertFalse((self.root/'ran').exists())
        result=await self.service().run('thread',permission_engine=self.engine)
        self.assertEqual(result['status'],'unverified'); self.assertFalse((self.root/'ran').exists())
    async def test_timeout_stdout_observation_and_cleanup(self):
        self.config([{'name':'hang','kind':'test','command':self.command('import time; time.sleep(9)'), 'timeout_seconds':1}])
        result=await self.run_steps(); self.assertTrue(result['steps'][0]['timed_out'])
        self.config([{'name':'server','kind':'run','command':self.command('import time; print("READY"); time.sleep(9)'),
                      'stdout_contains':'READY','timeout_seconds':2}])
        result=await self.run_steps(); self.assertEqual(result['status'],'passed')
        step=result['steps'][0]; self.assertTrue(step['observed']); self.assertNotEqual(step['exit_code'],0)
    async def test_http_and_cancellation_cleanup(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0)); port=listener.getsockname()[1]
        self.config([{'name':'http','kind':'run','command':shlex.quote(sys.executable)+f' -B -u -m http.server {port} --bind 127.0.0.1',
                     'http_url':f'http://127.0.0.1:{port}/','expected_status':200,'timeout_seconds':3}])
        result=await self.run_steps(); self.assertEqual(result['status'],'passed')
        with socket.socket() as connection: self.assertNotEqual(connection.connect_ex(('127.0.0.1',port)),0)
        self.config([{'name':'cancel','kind':'run','command':self.command('import time; time.sleep(30)'),
                      'stdout_contains':'never','timeout_seconds':30}])
        task=asyncio.create_task(self.run_steps()); await asyncio.sleep(.15); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.store.read_events('thread')[-1]['data']['status'],'cancelled')
    async def test_invalid_config_and_generated_input_hiding(self):
        for step in [ {'name':'run','kind':'run','command':'true'},
                      {'name':'url','kind':'run','command':'true','http_url':'http://localhost/','expected_status':200},
                      {'name':'time','kind':'test','command':'true','timeout_seconds':301},
                      {'name':'escape','kind':'test','command':'true','generated_paths':['../x']},
                      {'name':'conf','kind':'test','command':'true','generated_paths':['.nailong']} ]:
            self.config([step])
            with self.assertRaises(ValueError): self.service().list_steps()
        (self.root/'input').write_text('source')
        self.config([{'name':'test','kind':'test','command':'true','generated_paths':['input']}])
        with self.assertRaises(ValueError): await self.run_steps()
    async def test_declared_new_generated_output_is_allowed_but_mutation_invalid(self):
        self.config([{'name':'generate','kind':'test','command':self.command("open('build.log','w').write('generated')"),
                     'generated_paths':['build.log']}])
        self.assertEqual((await self.run_steps())['status'],'passed')
        self.assertEqual((await self.run_steps())['status'],'passed')
        (self.root/'input').write_text('before')
        self.config([{'name':'mutate','kind':'test','command':self.command("open('input','w').write('changed')")}])
        self.assertEqual((await self.run_steps())['status'],'invalidated')
    async def test_headless_verify_returns_machine_readable_evidence_without_model(self):
        from config import Settings
        from agent import AgentRuntimeFactory
        from headless import run_print
        from io import StringIO
        from unittest.mock import patch
        self.config([{'name':'test','kind':'test','command':self.command('print("ok")')}])
        settings=Settings('secret-token','https://api.invalid','deepseek-flash',self.root)
        factory=AgentRuntimeFactory(settings,session_store=self.store)
        runtime=factory()
        output=StringIO()
        with patch('headless.create_agent_runtime',return_value=runtime),patch.object(factory.model,'_agenerate',side_effect=AssertionError('Unexpected model request')):
            code=await run_print(settings,'/verify',output_format='json',stdout=output)
        payload=json.loads(output.getvalue())
        self.assertEqual(code,1); self.assertEqual(payload['status'],'approval_required')
        self.assertEqual(payload['workflow']['status'],'unverified')
