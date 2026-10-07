"""Completed non-read operations must interrupt the service's read-cycle history."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_service import AgentService
from nailong.core.permissions import PermissionEngine
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore


class ProgressServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        root = base / 'project'
        root.mkdir()
        store = ProjectSessionStore(root, base_dir=base / 'private')
        self.tasks = TaskStore(store)
        task = self.tasks.begin('owner', '排查功能')
        self.execution = SimpleNamespace()
        factory = SimpleNamespace(settings=SimpleNamespace(project_root=root), session_store=store,
            task_store=self.tasks, tool_execution_context=self.execution)
        self.service = AgentService(factory, permission_engine=PermissionEngine(root))
        token = self.service._turn_task.set(task)
        self.addCleanup(self.service._turn_task.reset, token)
        self.sequence = 0

    def observe(self, name, args, **fields):
        self.sequence += 1
        self.execution.observer({
            'thread_id': 'owner', 'name': name, 'call_id': str(self.sequence),
            'status': 'success', 'arguments_digest': args, 'result_digest': 'result',
            'input_version': 'v1', 'ok': True, 'changed': False, **fields,
        })

    def cycle(self):
        for args in ('A', 'B') * 2:
            self.observe('read_file', args)

    def test_command_and_edit_break_read_cycles_at_executor_boundary(self):
        for name in ('run_command', 'edit_file'):
            with self.subTest(name=name):
                self.tasks.reset_progress('owner')
                self.cycle()
                self.observe(name, 'operation', changed=name == 'edit_file')
                self.cycle()
                self.assertEqual(self.tasks.snapshot('owner')['lifecycle'], 'active')
                self.assertEqual(self.tasks.snapshot('owner')['progress_repeats'], 4)

    def test_missing_digest_is_a_barrier_and_hint_is_visible_to_the_model(self):
        self.cycle()
        self.assertIn('读取周期', self.tasks.snapshot('owner')['progress'])
        self.observe('read_file', None, result_digest=None)
        self.cycle()
        self.assertEqual(self.tasks.snapshot('owner')['lifecycle'], 'active')

    def test_failed_reads_break_the_cycle(self):
        self.cycle()
        self.observe('read_file', 'failed', ok=False, status='error')
        self.cycle()
        self.assertEqual(self.tasks.snapshot('owner')['lifecycle'], 'active')
