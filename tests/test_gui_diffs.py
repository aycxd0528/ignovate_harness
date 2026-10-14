import asyncio
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
from nailong.tools.files import FileSession


class FileDiffTests(unittest.TestCase):
    def test_write_records_committed_patch_and_full_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            files = FileSession(directory)
            created = files.write_file('a.txt', 'one\ntwo\n')
            self.assertEqual((created['additions'], created['deletions']), (2, 0))
            self.assertIn('+two', created['diff'])
            files.read_file('a.txt')
            edited = files.write_file('a.txt', 'one\nchanged\n')
            self.assertEqual((edited['additions'], edited['deletions']), (1, 1))
            self.assertIn('-two', edited['diff']); self.assertIn('+changed', edited['diff'])

    def test_large_patch_has_complete_counts_and_explicit_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            result = FileSession(directory).write_file('many.txt', 'line\n' * 5000)
            self.assertEqual(result['additions'], 5000)
            self.assertTrue(result['diff_truncated'])
            self.assertLessEqual(len(result['diff']), 12000)

    def test_write_records_final_newline_and_line_ending_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'a.txt'
            files = FileSession(directory)
            for before, after in [('hello', 'hello\n'), ('hello\r\n', 'hello\n')]:
                path.write_bytes(before.encode())
                files.read_file('a.txt')
                result = files.write_file('a.txt', after)
                self.assertEqual((result['additions'], result['deletions']), (1, 1))
                self.assertIn('-hello', result['diff'])
                self.assertIn('+hello', result['diff'])
                if not before.endswith('\n'):
                    self.assertIn('No newline at end of file', result['diff'])


class NativeDiffTests(unittest.IsolatedAsyncioTestCase):
    async def test_recorded_diff_survives_later_file_changes_and_map_origin_is_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); project = root/'project'; project.mkdir()
            with patch('pathlib.Path.home', return_value=root/'home'), patch('agent.ChatDeepSeek', return_value=OfflineModel(responses=[])):
                sessions = ProjectSessionStore(project, base_dir=root/'data')
                factory = AgentRuntimeFactory(Settings('test-key', 'https://example.test', 'deepseek-flash', project), session_store=sessions)
                controller = GUIController(project, sessions=sessions, service=AgentService(factory, api_key='test-key'))
                try:
                    branch = await controller.command('create', {})
                    result = await controller.command('map_send', {'branch_id':branch['id'],'kind':'root',
                        'message':'工具状态与产物测试','permission_mode':'bypassPermissions','request_id':'diff-map'})
                    await asyncio.wait_for(controller.worker, 10)
                    run = controller.runs[result['run_id']]
                    self.assertEqual(run['status'], 'completed', run.get('error'))
                    public = controller._public_run(run)
                    self.assertEqual(public['map_origin'], {'node_id':None,'kind':'root'})
                    self.assertEqual(public['request_id'], 'diff-map')
                    node = controller.snapshot()['nodes'][0]
                    tool = next(t for t in node['tools'] if t['name']=='write_file')
                    self.assertEqual((tool['additions'],tool['deletions']), (2,0))
                    self.assertIn('+# 测试产物', tool['diff'])
                    (project/'report.md').write_text('later outside change')
                    self.assertEqual(controller._node_detail(node['id'])['tools'][-1]['diff'], tool['diff'])
                    self.assertEqual(node['artifacts'][1]['additions'], 2)
                finally:
                    await controller.close()
