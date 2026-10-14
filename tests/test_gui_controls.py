import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.sessions import ProjectSessionStore
from nailong.gui.controller import GUIController
from gui_browser_fixture import OfflineModel


class GUIControlsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / 'one'; self.project.mkdir()
        self.home_patch = patch('pathlib.Path.home', return_value=self.root / 'home')
        self.home_patch.start()
        self.model_patch = patch('agent.ChatDeepSeek', side_effect=lambda **kw: OfflineModel(responses=[]))
        self.model_patch.start()
        sessions = ProjectSessionStore(self.project, base_dir=self.root / 'data')
        self.factory = AgentRuntimeFactory(Settings('offline-key', 'https://example.test', 'deepseek-flash', self.project), session_store=sessions)
        self.controller = GUIController(self.project, sessions=sessions, service=AgentService(self.factory, api_key='offline-key'))

    async def asyncTearDown(self):
        await self.controller.close()
        self.model_patch.stop(); self.home_patch.stop(); self.temp.cleanup()

    async def test_model_catalog_add_select_and_reset_effort_persist(self):
        await self.controller.command('reasoning', {'model':'deepseek-flash','effort':'max'})
        conf = await self.controller.command('model', {'action':'add','name':'Custom','model':'custom-model'})
        self.assertEqual(conf['model'], 'custom-model')
        self.assertEqual(conf['reasoning_effort'], 'default')
        self.assertEqual(conf['model_name'], 'Custom')
        self.assertIn({'name':'Custom','model':'custom-model'}, conf['models'])
        local = json.loads((self.project / '.nailong/settings.local.json').read_text())
        self.assertEqual(local['model'], 'Custom')
        self.assertEqual(local['reasoning_effort'], 'default')
        self.assertNotIn('offline-key', json.dumps(local))
        conf = await self.controller.command('model', {'action':'select','name':'default'})
        self.assertEqual(conf['model'], 'deepseek-flash')
        with self.assertRaises(ValueError):
            await self.controller.command('model', {'action':'add','name':'Custom','model':'duplicate'})
        self.assertEqual(self.controller.configuration()['model'], 'deepseek-flash')

    async def test_model_change_rejects_active_queue_and_stale_project(self):
        self.controller.queue.append('test')
        with self.assertRaisesRegex(ValueError, '运行'):
            await self.controller.command('model', {'action':'add','name':'Busy','model':'x'})
        self.controller.queue.clear()
        with self.assertRaisesRegex(ValueError, '切换'):
            await self.controller.command('model', {'action':'select','name':'default','project_id':'wrong'})

    async def test_workspace_catalog_reads_existing_metadata_without_creating_db(self):
        other = self.root / 'two'; other.mkdir()
        sessions = ProjectSessionStore(other, base_dir=self.root / 'data')
        from nailong.core.branches import BranchStore
        branches = BranchStore(sessions)
        branch = branches.create('Second workspace'); branches.close()
        empty = self.root / 'empty'; empty.mkdir()
        empty_sessions = ProjectSessionStore(empty, base_dir=self.root / 'data')
        connections = []
        connect = sqlite3.connect
        def record_connection(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connections.append(connection)
            return connection
        with patch('nailong.gui.controls.sqlite3.connect', side_effect=record_connection):
            result = await self.controller.command('workspaces', {'paths':[str(other),str(empty)]})
        self.assertEqual(result[0]['branches'][0]['id'], branch['id'])
        self.assertEqual(result[0]['branches'][0]['name'], 'Second workspace')
        self.assertEqual(result[1]['branches'], [])
        self.assertFalse(empty_sessions.root.exists())
        self.assertEqual(self.controller.project, self.project.resolve())
        self.assertEqual(len(connections), 1)
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute('SELECT 1')

    async def test_folder_reference_is_bounded_and_does_not_read_contents(self):
        (self.project/'src').mkdir(); (self.project/'src/code.py').write_text('secret source')
        result = await self.controller.command('folder', {'path':'src'})
        self.assertIn('code.py', str(result))
        self.assertNotIn('secret source', str(result))
        with self.assertRaises(ValueError):
            await self.controller.command('folder', {'path':'../'})

    async def test_goal_send_uses_native_bounded_driver(self):
        branch = await self.controller.command('create', {})
        result = await self.controller.command('send', {'branch_id':branch['id'],'message':'检查项目',
            'profile':'goal','goal_limits':{'max_rounds':1,'max_cost_usd':0.05},'request_id':'bounded-goal'})
        await asyncio.wait_for(self.controller.worker, 10)
        run = self.controller.runs[result['run_id']]
        self.assertEqual(run['status'], 'completed', run.get('error'))
        goal = self.factory.goal_store.latest()
        self.assertEqual(goal.thread_id, branch['thread_id'])
        self.assertEqual(goal.max_rounds, 1)
        self.assertEqual(goal.objective, '检查项目')
        self.assertEqual(goal.round, 1)
        self.assertNotEqual(goal.state, 'active')

    async def test_invalid_goal_limits_do_not_enqueue(self):
        branch = await self.controller.command('create', {})
        with self.assertRaises(ValueError):
            await self.controller.command('send', {'branch_id':branch['id'],'message':'goal',
                'profile':'goal','goal_limits':{'max_rounds':999,'max_cost_usd':float('nan')},'request_id':'bad-goal'})
        self.assertEqual(self.controller.queue, [])

    async def test_stopped_goal_recovery_keeps_same_goal_and_remaining_limits(self):
        from agent_service import TurnEvent
        branch = await self.controller.command('create', {})
        started = asyncio.Event()
        async def blocked(*args, **kwargs):
            yield TurnEvent('usage', {'input_tokens':10,'output_tokens':2,'cache_hit_tokens':0})
            started.set()
            await asyncio.Event().wait()
        with patch.object(self.controller.service, 'stream_turn', side_effect=blocked):
            result = await self.controller.command('send', {'branch_id':branch['id'],'message':'继续检查',
                'profile':'goal','goal_limits':{'max_rounds':3,'max_cost_usd':0.2},'request_id':'goal-stop'})
            await asyncio.wait_for(started.wait(), 5)
            stopped = await self.controller.command('stop', {'run_id':result['run_id']})
        goal = self.factory.goal_store.latest()
        self.assertEqual(stopped['goal']['id'], goal.id)
        self.assertEqual(stopped['goal']['state'], 'paused')
        self.controller.paused = True
        # Capture enqueue without launching another model turn.
        resumed = await self.controller.command('resume_run', {'run_id':result['run_id'],'request_id':'goal-resume'})
        payload = self.controller.runs[resumed['run_id']]['payload']
        self.assertEqual(payload['profile'], 'goal')
        self.assertEqual(payload['goal_id'], goal.id)
        self.assertEqual(payload['goal_limits'], {'max_rounds':3,'max_cost_usd':0.2})
