import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.sessions import ProjectSessionStore
from nailong.core.skills import SkillRegistry
from nailong.gui.controller import GUIController
from gui_browser_fixture import OfflineModel


class GUIPluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root/'project'; self.project.mkdir()
        self.user_skills = self.root/'user-skills'; self.user_skills.mkdir()
        self.registry_patch = patch('pathlib.Path.home', return_value=self.root)
        self.registry_patch.start()
        self.sessions = ProjectSessionStore(self.project, base_dir=self.root/'data')
        with patch('agent.ChatDeepSeek', return_value=OfflineModel(responses=[])):
            self.factory = AgentRuntimeFactory(Settings('test-key','https://example.test','unknown',self.project), session_store=self.sessions)
        self.controller = GUIController(self.project, sessions=self.sessions, service=AgentService(self.factory,api_key='test-key'))

    async def asyncTearDown(self):
        await self.controller.close()
        self.registry_patch.stop()
        self.temp.cleanup()

    def package(self, name='example-skill'):
        path=self.root/name; path.mkdir()
        (path/'SKILL.md').write_text(f'---\nname: {name}\ndescription: Useful local instructions\n---\n# Task instructions\n')
        (path/'resource.txt').write_text('real resource')
        return path

    async def mutate(self, operation, **payload):
        return await self.controller.command('plugin', {'operation':operation, **payload})

    async def test_listing_does_not_initialize_model_or_execute_mcp(self):
        await self.controller.close()
        self.controller=GUIController(self.project,sessions=self.sessions)
        from nailong.mcp.config import MCPConfigStore
        MCPConfigStore(self.project).add('test',{'transport':'stdio','command':'never-execute-this'})
        with patch.object(self.controller,'ensure_service',side_effect=AssertionError('listing must stay offline')):
            result=await self.controller.command('plugins',{})
        self.assertEqual(result['mcp'][0]['status'],'disconnected')
        self.assertIsNone(self.controller.service)

    async def test_skill_install_and_toggle_change_native_catalog_and_persist(self):
        source=self.package()
        result=await self.mutate('skill_install',path=str(source))
        self.assertEqual(result['skills'][0]['name'],'example-skill')
        self.assertTrue(result['skills'][0]['enabled'])
        self.assertEqual((self.project/'.agents/skills/example-skill/resource.txt').read_text(),'real resource')
        self.assertIn('example-skill',self.factory._skill_catalog)
        result=await self.mutate('skill_enable',name='example-skill',enabled=False)
        self.assertFalse(result['skills'][0]['enabled'])
        self.assertNotIn('example-skill',self.factory._skill_catalog)
        self.assertFalse(self.factory.skill_registry.load_skill('example-skill')['ok'])
        self.assertIn('example-skill',json.loads((self.project/'.nailong/settings.local.json').read_text())['disabled_skills'])
        await self.mutate('skill_enable',name='example-skill',enabled=True)
        self.assertTrue(self.factory.skill_registry.load_skill('example-skill')['ok'])
        with self.assertRaises(ValueError):
            await self.mutate('skill_install',path=str(source))

    async def test_invalid_skill_packages_have_no_installation_effect(self):
        source=self.package()
        target=self.project/'.agents/skills/example-skill'
        for damage in ('symlink','huge','invalid'):
            resource=source/'resource.txt';resource.unlink()
            if damage=='symlink':resource.symlink_to(source/'SKILL.md')
            elif damage=='huge':resource.write_bytes(b'x'*65537)
            else:
                resource.write_text('fine');(source/'SKILL.md').write_text('---\nname: wrong-name\ndescription: bad\n---\n')
            with self.assertRaisesRegex(ValueError, '符号链接|大小|64 KiB|name'):
                await self.mutate('skill_install',path=str(source))
            self.assertFalse(target.exists())

    async def test_skill_install_rejects_link_target_parent(self):
        source=self.package()
        outside=self.root/'outside';outside.mkdir()
        (self.project/'.agents').symlink_to(outside,target_is_directory=True)
        with self.assertRaises((ValueError,OSError)):
            await self.mutate('skill_install',path=str(source))
        self.assertEqual(list(outside.iterdir()),[])

    async def test_mcp_updates_validate_before_disconnect_and_keep_original_on_failure(self):
        original={'transport':'http','url':'http://127.0.0.1:1/mcp','headers':{'Authorization':'${MCP_LOCAL_TOKEN}'}}
        result=await self.mutate('mcp_add',name='test',definition=original)
        self.assertEqual(result['mcp'][0]['definition'],original)
        with self.assertRaisesRegex(ValueError,'凭据'):
            await self.mutate('mcp_update',name='test',definition={**original,'headers':{'Authorization':'plaintext-secret'}})
        self.assertEqual(self.factory.mcp_manager.store.list_servers()['test'],original)
        result=await self.mutate('mcp_update',name='test',definition={**original,'url':'http://127.0.0.1:2/mcp'})
        self.assertEqual(result['mcp'][0]['definition']['url'],'http://127.0.0.1:2/mcp')
        await self.mutate('mcp_remove',name='test')
        self.assertEqual(self.factory.mcp_manager.store.list_servers(),{})

    async def test_mcp_connect_and_disconnect_use_native_runtime_manager(self):
        fixture=Path(__file__).parent/'fixtures/mcp_server.py'
        await self.mutate('mcp_add',name='test',definition={'transport':'stdio','command':sys.executable,'args':[str(fixture)],'timeout_seconds':10})
        self.assertEqual(self.factory.mcp_manager.tools(),())
        result=await self.mutate('mcp_connect',name='test')
        self.assertEqual(result['mcp'][0]['status'],'connected')
        self.assertGreater(result['mcp'][0]['tool_count'],0)
        self.assertIn('echo',[tool.name for tool in self.factory.mcp_manager.tools('test')])
        result=await self.mutate('mcp_disconnect',name='test')
        self.assertEqual(result['mcp'][0]['status'],'disconnected')
        self.assertEqual(self.factory.mcp_manager.tools(),())

    async def test_plugin_mutations_reject_busy_and_stale_project_without_changes(self):
        source=self.package()
        with self.assertRaisesRegex(ValueError,'项目'):
            await self.controller.command('plugin',{'operation':'skill_install','path':str(source),'project_id':'stale'})
        self.controller.queue.append('fake-queued-run')
        with self.assertRaisesRegex(ValueError,'运行|队列'):
            await self.mutate('skill_install',path=str(source))
        self.controller.queue.clear()
        self.assertFalse((self.project/'.agents/skills/example-skill').exists())
        with self.assertRaises(ValueError):
            await self.mutate('skill_enable',name='example-skill',enabled='false')
