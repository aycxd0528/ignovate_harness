import asyncio
import importlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from config import Settings
from ui.controller import CommandController

class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def runner(self):
        try: cls=importlib.import_module('nailong.core.runner').SessionRunner
        except ModuleNotFoundError: self.fail('Session runner missing')
        return cls('project','thread')
    async def test_order_capacity_and_identity(self):
        runner=self.runner(); self.addAsyncCleanup(runner.close)
        started=asyncio.Event(); release=asyncio.Event(); seen=[]
        async def first(): started.set(); await release.wait(); seen.append('first')
        runner.submit('first',first); await started.wait()
        for index in range(10):
            async def job(index=index): seen.append(index)
            runner.submit(str(index),job)
        with self.assertRaises(ValueError): runner.submit('overflow',first)
        with self.assertRaises(ValueError): runner.submit('other',first,thread_id='other')
        with self.assertRaises(ValueError): runner.rebind('project','other')
        release.set(); await runner.wait_idle()
        self.assertEqual(seen,['first',*range(10)])
        runner.rebind('project','other'); self.assertEqual(runner.thread_id,'other')
    async def test_cancel_pauses_queue_and_requires_explicit_resume(self):
        runner=self.runner(); self.addAsyncCleanup(runner.close)
        started=asyncio.Event(); cleaned=asyncio.Event(); seen=[]
        async def first():
            started.set()
            try: await asyncio.Event().wait()
            finally: cleaned.set()
        runner.submit('first',first); await started.wait()
        async def next_job(): seen.append('next')
        runner.submit('next',next_job)
        await runner.stop(); self.assertTrue(cleaned.is_set()); self.assertEqual(runner.state,'paused'); self.assertFalse(seen)
        runner.resume(); await runner.wait_idle(); self.assertEqual(seen,['next'])
    async def test_remove_clear_and_failed_item_do_not_corrupt_next(self):
        runner=self.runner(); runner.state='paused'; self.addAsyncCleanup(runner.close)
        async def job(): return 1
        runner.submit('one',job); runner.submit('two',job)
        runner.remove(1); self.assertEqual(runner.queue[0].message,'two')
        runner.clear(); self.assertFalse(runner.queue)
        async def fail(): raise ValueError('fixture')
        future=runner.submit('failure',fail); success=runner.submit('next',job); runner.resume()
        with self.assertRaises(ValueError): await future
        self.assertEqual(await success,1)

class WorkflowCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.settings=Settings('key','https://api.invalid','deepseek-flash',Path(self.directory.name).resolve())
        self.controller=CommandController(self.settings)
    async def test_all_workflow_commands_are_registered_and_default_review_resolves(self):
        names={spec.name for spec in self.controller.specs()}
        required={'/diff','/verify','/doctor','/status','/reload-skills','/stop','/queue','/rename','/recap','/export','/model','/theme','/config','/memory','/tools'}
        self.assertTrue(required<=names,required-names)
        self.assertEqual(self.controller.resolve('/review').kind,'local')
        self.assertEqual(self.controller.resolve('/review --staged').kind,'local')
    async def test_actions_offline_and_configuration_are_local(self):
        try: cls=importlib.import_module('ui.actions').CommandActions
        except ModuleNotFoundError: self.fail('Shared actions missing')
        actions=cls(self.controller)
        factory=SimpleNamespace(settings=self.settings)
        service=SimpleNamespace(runtime_factory=factory,session_store=None,permission_mode='default')
        for command in ['/doctor','/status','/config','/theme light']:
            result=await actions.execute(self.controller.resolve(command),service,'thread')
            self.assertTrue(result.text); self.assertFalse(result.model_requests)
        with self.assertRaises(ValueError): await actions.execute(self.controller.resolve('/diff --unknown'),service,'thread')
        with self.assertRaises(ValueError): await actions.execute(self.controller.resolve('/config api_base http://evil'),service,'thread')
    async def test_documented_config_set_and_memory_show_scope(self):
        from ui.actions import CommandActions
        actions=CommandActions(self.controller)
        service=SimpleNamespace(runtime_factory=SimpleNamespace(settings=self.settings),session_store=None,permission_mode='default')
        result=await actions.execute(self.controller.resolve('/config set output_style concise'),service,'thread')
        self.assertEqual(result.data['output_style'],'concise')
        result=await actions.execute(self.controller.resolve('/memory show project'),service,'thread')
        self.assertIn('project',result.text)
