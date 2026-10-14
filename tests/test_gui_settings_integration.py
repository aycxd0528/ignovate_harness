import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from gui_browser_fixture import OfflineModel
from nailong.core.sessions import ProjectSessionStore
from nailong.gui.controller import GUIController


class SettingsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.project=self.root/'project';self.project.mkdir()
        self.home=patch('pathlib.Path.home',return_value=self.root/'home');self.home.start()
        self.env=patch.dict('os.environ',{'IGNOVATE_CONFIG_DIR':str(self.root/'home/config')});self.env.start()
        self.models=patch('agent.ChatDeepSeek',side_effect=lambda **kw:OfflineModel(responses=[]));self.models.start()
        self.sessions=ProjectSessionStore(self.project,base_dir=self.root/'data')
        self.factory=AgentRuntimeFactory(Settings('settings-test-key','https://example.test','deepseek-flash',self.project),session_store=self.sessions)
        self.controller=GUIController(self.project,sessions=self.sessions,service=AgentService(self.factory,api_key='settings-test-key'))

    async def asyncTearDown(self):
        await self.controller.close();self.models.stop();self.env.stop();self.home.stop();self.temp.cleanup()

    async def test_default_permission_applies_only_to_new_conversations_and_survives_restart(self):
        old=await self.controller.command('create',{})
        await self.controller.command('settings_update',{'changes':{'default_permission':'acceptEdits'}})
        new=await self.controller.command('create',{})
        modes={b['id']:b['permission_mode'] for b in self.controller.snapshot()['branches']}
        self.assertEqual(modes[old['id']],'default');self.assertEqual(modes[new['id']],'acceptEdits')
        self.controller.paused=True
        queued=await self.controller.command('send',{'branch_id':new['id'],'message':'queued','request_id':'queued-settings'})
        await self.controller.command('settings_update',{'changes':{'default_permission':'bypassPermissions'}})
        self.assertEqual(self.controller.runs[queued['run_id']]['payload']['permission_mode'],'acceptEdits')
        await self.controller.command('stop',{'run_id':queued['run_id']})
        await self.controller.command('permission',{'branch_id':new['id'],'mode':'default'})
        await self.controller.close()
        self.factory=AgentRuntimeFactory(Settings('settings-test-key','https://example.test','deepseek-flash',self.project),session_store=self.sessions)
        self.controller=GUIController(self.project,sessions=self.sessions,service=AgentService(self.factory,api_key='settings-test-key'))
        modes={b['id']:b['permission_mode'] for b in self.controller.snapshot()['branches']}
        self.assertEqual(modes[new['id']],'default')
        latest=await self.controller.command('create',{})
        self.assertEqual(next(b['permission_mode'] for b in self.controller.snapshot()['branches'] if b['id']==latest['id']),'bypassPermissions')

    async def test_new_fork_keeps_permission_isolation_despite_new_chat_default(self):
        branch=await self.controller.command('create',{})
        result=await self.controller.command('send',{'branch_id':branch['id'],'message':'start','request_id':'settings-head'})
        await asyncio.wait_for(self.controller.worker,10)
        await self.controller.command('settings_update',{'changes':{'default_permission':'bypassPermissions'}})
        fork=await self.controller.command('fork',{'branch_id':branch['id'],'node_id':self.controller.runs[result['run_id']]['node_id'],'name':'fork'})
        self.assertEqual(next(b['permission_mode'] for b in self.controller.snapshot()['branches'] if b['id']==fork['id']),'default')

    async def test_settings_scope_snapshot_and_secret_safe_provider_catalog(self):
        await self.controller.command('settings_update',{'changes':{'font_size':18,'tool_details':'detailed','show_changes':False}})
        values=self.controller.snapshot()['gui_settings']
        self.assertEqual(values['preferences']['font_size'],18)
        self.assertIn('font_size',values['saved_keys'])
        catalog=await self.controller.command('provider',{'action':'save','name':'Second','api_base':'https://second.example/v1','model':'test-model','api_key':'never-export-provider-key'})
        self.assertNotIn('never-export-provider-key',json.dumps(catalog))
        self.assertNotIn('settings-test-key',json.dumps(await self.controller.command('settings',{})))
        self.assertEqual(self.controller.configuration()['api_base'],'https://example.test')
        with self.assertRaisesRegex(ValueError,'切换'):
            await self.controller.command('settings_update',{'changes':{'font_size':14},'project_id':'wrong'})
