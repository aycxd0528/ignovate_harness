import asyncio
import json
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


class NativeGUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / 'project'; self.project.mkdir()
        self.sessions = ProjectSessionStore(self.project, base_dir=Path(self.temp.name) / 'data')
        self.model = OfflineModel(responses=[])
        with patch('agent.ChatDeepSeek', return_value=self.model):
            self.factory = AgentRuntimeFactory(Settings('test-key', 'https://example.test', 'unknown', self.project), session_store=self.sessions)
        self.controller = GUIController(self.project, sessions=self.sessions, service=AgentService(self.factory, api_key='test-key'))
        self.root = await self.controller.command('create', {'name': '主分支'})

    async def asyncTearDown(self):
        await self.controller.close(); self.temp.cleanup()

    async def send(self, branch, message):
        result = await self.controller.command('send', {'branch_id': branch['id'], 'message': message, 'request_id': message[:120]})
        async def wait():
            while self.controller.runs[result['run_id']]['status'] in {'queued', 'running', 'waiting_approval'}:
                await asyncio.sleep(.01)
        await asyncio.wait_for(wait(), 10)
        run = self.controller.runs[result['run_id']]
        self.assertEqual(run['status'], 'completed', run.get('error'))
        return self.controller.branches.node(run['node_id'])

    async def test_native_fork_preserves_task_and_excludes_later_source_content(self):
        first = await self.send(self.root, '共同设计要求')
        await self.send(self.root, 'A_ONLY_LATER_REQUIREMENT')
        child = await self.controller.command('fork', {'branch_id': self.root['id'], 'node_id': first['id'], 'name': 'B'})
        await self.send(child, 'B_ONLY_REQUIREMENT')
        history = self.controller.service.get_history(child['thread_id'])
        self.assertIn('共同设计要求', str(history))
        self.assertNotIn('A_ONLY_LATER_REQUIREMENT', str(history))
        task = self.factory.task_store.snapshot(child['thread_id'])
        self.assertNotIn('A_ONLY_LATER_REQUIREMENT', json.dumps(task, ensure_ascii=False))
        self.assertEqual([n['prompt'] for n in self.controller.branches.path(child['id'])], ['共同设计要求', 'B_ONLY_REQUIREMENT'])

    async def test_map_followup_from_history_forks_exactly_once_on_retry(self):
        first = await self.send(self.root, '共同输入')
        await self.send(self.root, 'MAIN_ONLY_LATER')
        payload = {'branch_id': self.root['id'], 'node_id': first['id'], 'kind': 'continue',
                   'message': 'MAP_ONLY_FOLLOWUP', 'request_id': 'map-retry'}
        result = await self.controller.command('map_send', payload)
        repeated = await self.controller.command('map_send', payload)
        self.assertEqual(result, repeated)
        self.assertEqual(len(self.controller.branches.branches()), 2)
        run = self.controller.runs[result['run_id']]
        while run['status'] in {'queued', 'running', 'waiting_approval'}:
            await asyncio.sleep(.01)
        self.assertEqual(run['status'], 'completed', run.get('error'))
        history = str(self.controller.service.get_history(self.controller.branches.branch(run['branch_id'])['thread_id']))
        self.assertNotIn('MAIN_ONLY_LATER', history)
        self.assertIn('MAP_ONLY_FOLLOWUP', history)
        with self.assertRaisesRegex(ValueError, '请求 ID'):
            await self.controller.command('map_send', {**payload, 'message': 'different'})

    async def test_invalid_map_input_does_not_create_a_branch(self):
        first = await self.send(self.root, '已有回合')
        with self.assertRaisesRegex(ValueError, '输入'):
            await self.controller.command('map_send', {'branch_id': self.root['id'], 'node_id': first['id'],
                'kind': 'fork', 'message': '', 'request_id': 'empty-map'})
        self.assertEqual(len(self.controller.branches.branches()), 1)

    async def test_map_request_recovers_chosen_fork_after_enqueue_failure_and_restart(self):
        first = await self.send(self.root, '共享起点')
        payload = {'branch_id': self.root['id'], 'node_id': first['id'], 'kind': 'fork',
                   'message': '持久化重试', 'request_id': 'map-durable-retry'}
        with patch.object(self.controller, '_enqueue', side_effect=OSError('injected storage failure')):
            with self.assertRaises(OSError):
                await self.controller.command('map_send', payload)
        self.assertEqual(len(self.controller.branches.branches()), 2)
        await self.controller.close()
        with patch('agent.ChatDeepSeek', return_value=OfflineModel(responses=[])):
            self.factory = AgentRuntimeFactory(Settings('test-key', 'https://example.test', 'unknown', self.project), session_store=self.sessions)
        self.controller = GUIController(self.project, sessions=self.sessions, service=AgentService(self.factory, api_key='test-key'))
        result = await self.controller.command('map_send', payload)
        self.assertEqual(len(self.controller.branches.branches()), 2, 'retry must reuse the durable chosen child')
        self.assertEqual(await self.controller.command('map_send', payload), result)

    async def test_map_draft_permissions_apply_to_new_target_without_editing_source(self):
        first = await self.send(self.root, '权限起点')
        await self.controller.command('permission', {'branch_id': self.root['id'], 'mode': 'bypassPermissions'})
        result = await self.controller.command('map_send', {'branch_id': self.root['id'], 'node_id': first['id'],
            'kind': 'fork', 'message': '明确选择的分支权限', 'permission_mode': 'acceptEdits', 'request_id': 'map-permission'})
        run = self.controller.runs[result['run_id']]
        self.assertEqual(run['payload']['permission_mode'], 'acceptEdits')
        self.assertEqual(self.controller.branch_modes[self.root['id']], 'bypassPermissions')
        self.assertEqual(self.controller.branch_modes[run['branch_id']], 'acceptEdits')

    async def test_long_answer_details_are_complete_after_reload_without_loading_model(self):
        node = await self.send(self.root, '长回答')
        detail = await self.controller.command('node', {'node_id': node['id']})
        self.assertIn('正文末尾标记', detail['answer'])
        # Close/reopen GUI while retaining all native checkpoint bytes.
        await self.controller.close()
        self.controller = GUIController(self.project, sessions=self.sessions)
        detail = await self.controller.command('node', {'node_id': node['id']})
        self.assertIn('正文末尾标记', detail['answer'])
        self.assertIsNone(self.controller.service)

    async def test_reasoning_slider_updates_native_model_and_persists_project_preference(self):
        from dataclasses import replace
        self.factory.settings=replace(self.factory.settings,model='deepseek-flash')
        self.factory.preferences.default_model='deepseek-flash'
        with patch('agent.ChatDeepSeek',return_value=self.model):
            result=await self.controller.command('reasoning',{'model':'deepseek-flash','effort':'max'})
        self.assertEqual(result['reasoning_effort'],'max')
        self.assertEqual(self.factory.settings.reasoning_effort,'max')
        saved=json.loads((self.project/'.nailong/settings.local.json').read_text())
        self.assertEqual(saved['reasoning_effort'],'max')
        self.assertNotIn('provider',saved)
        await self.controller.close()
        with patch('config.load_settings',return_value=Settings('test-key','https://example.test','deepseek-flash',self.project)):
            self.controller=GUIController(self.project,sessions=self.sessions)
            configuration=self.controller.snapshot()['configuration']
        self.assertEqual(configuration['reasoning_effort'],'max')
        self.assertIsNone(self.controller.service)

    async def test_reasoning_control_rejects_unsupported_or_stale_model_and_busy_queue(self):
        with self.assertRaisesRegex(ValueError,'推理|模型'):
            await self.controller.command('reasoning',{'model':'unknown','effort':'max'})
        with self.assertRaisesRegex(ValueError,'模型'):
            await self.controller.command('reasoning',{'model':'deepseek-flash','effort':'default'})
        self.controller.paused=True
        await self.controller.command('send',{'branch_id':self.root['id'],'message':'queued','request_id':'reasoning-busy'})
        with self.assertRaisesRegex(ValueError,'运行|队列|排队'):
            await self.controller.command('reasoning',{'model':'unknown','effort':'default'})
        self.assertFalse((self.project/'.nailong/settings.local.json').exists())

    async def test_model_settings_replace_slider_preference_and_allow_default_only_model(self):
        import os
        from dataclasses import replace
        self.factory.settings=replace(self.factory.settings,model='deepseek-flash')
        self.factory.preferences.default_model='deepseek-flash'
        with patch.dict(os.environ,{'IGNOVATE_CONFIG_DIR':str(Path(self.temp.name)/'config')}),patch('agent.ChatDeepSeek',return_value=self.model):
            await self.controller.command('reasoning',{'model':'deepseek-flash','effort':'max'})
            result=await self.controller.command('configuration',{'api_base':'https://example.test','model':'deepseek-flash','reasoning_effort':'low'})
            self.assertEqual(result['reasoning_effort'],'low')
            result=await self.controller.command('configuration',{'api_base':'https://example.test','model':'unknown','reasoning_effort':'default'})
            self.assertTrue(result['configured'])
            self.assertEqual(result['model'],'unknown')
            self.assertEqual(result['reasoning_effort'],'default')
            self.controller.ensure_service()
            self.assertEqual(self.controller.factory.settings.model,'unknown')
            self.assertEqual(self.controller.factory.settings.reasoning_effort,'default')

    async def test_fork_preserves_multi_revision_requirements_and_task_archive_lookup(self):
        from nailong.core.task_history import TaskContextHistory
        self.factory.task_store.begin(self.root['thread_id'], 'first requirement')
        await self.send(self.root, 'first requirement')
        self.factory.task_store.begin(self.root['thread_id'], 'second requirement')
        second = await self.send(self.root, 'second requirement')
        source_task = self.factory.task_store.snapshot(self.root['thread_id'])
        self.assertGreater(source_task['revision'], 1)
        archived = TaskContextHistory(self.factory.history_archive, self.root['thread_id'], self.project).save(source_task)
        from nailong.core.branches import ForkHistory
        mapped = ForkHistory(self.factory.history_archive, self.root['thread_id'], 'archive-child').copy(archived)
        read = TaskContextHistory(self.factory.history_archive, 'archive-child', self.project).read(mapped, section='requirements')
        self.assertIn('second requirement', read['content'])
        remapped = ForkHistory(self.factory.history_archive, 'archive-child', 'archive-grandchild').copy(mapped)
        read = TaskContextHistory(self.factory.history_archive, 'archive-grandchild', self.project).read(remapped, section='requirements')
        self.assertIn('second requirement', read['content'])
        child = await self.controller.command('fork', {'branch_id': self.root['id'], 'node_id': second['id'], 'name': 'B'})
        task = self.factory.task_store.snapshot(child['thread_id'])
        self.assertEqual(task['request_history'], source_task['request_history'])
        self.assertGreater(task['revision'], source_task['revision'])

    async def test_verify_uses_real_core_events_and_keeps_result_in_details(self):
        from platform_fixtures import python_command
        await self.send(self.root, 'design')
        (self.project / '.nailong').mkdir(exist_ok=True)
        (self.project / '.nailong/settings.json').write_text(json.dumps({'verification': {'steps': [
            {'name': 'unit', 'kind': 'test', 'command': python_command('print("ok")')}]}}))
        result = await self.controller.command('verify', {'branch_id': self.root['id'], 'request_id': 'native-verify'})
        async def approve():
            while not self.controller.approvals:
                await asyncio.sleep(.01)
            identity = next(iter(self.controller.approvals))
            await self.controller.command('approve', {'action_id': identity, 'run_id': result['run_id'], 'decision': 'approve_once'})
            await self.controller.worker
        await asyncio.wait_for(approve(), 10)
        run = self.controller.runs[result['run_id']]
        self.assertEqual(run['status'], 'completed', run.get('error'))
        detail = await self.controller.command('node', {'node_id': run['node_id']})
        self.assertEqual(json.loads(detail['answer'])['status'], 'passed')
        (self.project / 'source.txt').write_text('changed')
        detail = await self.controller.command('node', {'node_id': run['node_id']})
        self.assertEqual(detail['delivery']['status'], 'stale')

    async def test_plan_can_be_explicitly_approved_after_restart(self):
        result = await self.controller.command('send', {'branch_id': self.root['id'], 'message': 'plan a change', 'profile': 'plan', 'request_id': 'plan-test'})
        await self.controller.worker
        self.assertTrue(self.controller.runs[result['run_id']]['plan_id'])
        self.assertFalse((self.project/'.nailong/plans').exists())
        await self.controller.close()
        with patch('agent.ChatDeepSeek', return_value=self.model):
            self.factory = AgentRuntimeFactory(Settings('test-key', 'https://example.test', 'unknown', self.project), session_store=self.sessions)
        self.controller = GUIController(self.project, sessions=self.sessions, service=AgentService(self.factory, api_key='test-key'))
        response = await self.controller.command('accept_plan', {'run_id': result['run_id'], 'request_id': 'approved-plan'})
        await self.controller.worker
        self.assertEqual(self.controller.runs[response['run_id']]['status'], 'completed')
        self.assertEqual(len(list((self.project/'.nailong/plans').glob('*.md'))), 1)
