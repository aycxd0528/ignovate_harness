import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import Settings
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
from ui.actions import CommandActions
from ui.controller import CommandRequest


class WorkControlCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'project'; self.root.mkdir()
        self.sessions = ProjectSessionStore(self.root, base_dir=Path(self.temp.name) / 'data')
        self.tasks = TaskStore(self.sessions)
        self.task = self.tasks.begin('owner', '修复入口')
        self.settings = Settings('offline', 'https://example.invalid', 'deepseek-flash', self.root)
        with patch('pathlib.Path.home', return_value=Path(self.temp.name)):
            self.actions = CommandActions(SimpleNamespace(settings=self.settings))
        self.service = SimpleNamespace(session_store=self.sessions, task_store=self.tasks,
            runtime_factory=SimpleNamespace(settings=self.settings, task_store=self.tasks, goal_store=None))

    async def command(self, text, thread='owner'):
        return await self.actions.execute(CommandRequest('/task', text, '/task '+text), self.service, thread)

    async def test_list_and_control_return_same_durable_work_item(self):
        listed = await self.command('list')
        self.assertEqual(listed.data['items'][0]['task_id'], self.task['task_id'])
        observed = await self.command('control')
        self.assertEqual(observed.data['task']['task_id'], self.task['task_id'])
        self.assertEqual(observed.data['receipts'], [])

    async def test_cli_wait_ready_and_handoff_require_revision(self):
        await self.command(f"link {self.task['task_id']} {self.task['revision']}", thread='target')
        revision = self.tasks.snapshot('target')['revision']
        await self.command(f'wait {revision} "等待 CI" "结果更新"', thread='target')
        self.tasks.amend('owner', constraints=['保留现有接口'])
        with self.assertRaises(ValueError):
            await self.command(f'ready {revision}', thread='target')
        updated = self.tasks.snapshot('target')
        await self.command(f"ready {updated['revision']}", thread='target')
        self.assertIsNone(self.tasks.snapshot('owner').get('wait_condition'))

    async def test_control_does_not_project_another_sessions_goal_budget(self):
        from nailong.core.goal import GoalStore
        goals = GoalStore(Path(self.temp.name) / 'goals.json', project_root=self.root)
        goals.create(self.task['objective'], thread_id='different')
        self.service.runtime_factory.goal_store = goals
        observed = await self.command('control')
        self.assertIsNone(observed.data['goal'])
        self.assertEqual(observed.data['continuation']['action'], 'unknown')
