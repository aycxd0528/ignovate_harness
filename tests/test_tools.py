import importlib
import json
import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from nailong.tools.files import FileSession
from nailong.tools.execution import ToolExecutionContext
from nailong.tools.registry import build_tool_specs
import local_tools


def import_tools(test_case):
    try:
        return importlib.import_module("tools")
    except ModuleNotFoundError as error:
        test_case.fail(f"tools module is missing: {error}")


class LangChainToolsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.session = FileSession(self.root)

    def build(self, *, approved=False, **kwargs):
        session = kwargs.pop('file_session', self.session)
        execution = ToolExecutionContext(session.project_root,
            api_key=kwargs.get('api_key', ''),
            approval_handler=(lambda *args: {'type': 'approve'}) if approved else None)
        return {tool.name: tool for tool in import_tools(self).build_tools(
            file_session=session, execution_context=execution, **kwargs)}

    def test_goal_tool_cannot_mutate_unbound_or_other_chat_goal(self):
        from nailong.core.goal import GoalStore
        with tempfile.TemporaryDirectory() as directory:
            store=GoalStore(Path(directory)/'goals.json')
            goal=store.create('isolated objective')
            for thread_id in ('ordinary-chat',None):
                with self.subTest(thread_id=thread_id):
                    tool=self.build(goal_store=store,thread_id=thread_id)['update_goal']
                    result=json.loads(tool.invoke({'state':'paused','summary':'unrelated caller'}))
                    self.assertFalse(result['ok'],'未绑定目标不能由普通或匿名会话的工具更新')
                    self.assertEqual(store.active().id,goal.id)
            store.attach_thread(goal.id,'owner-chat')
            tool=self.build(goal_store=store,thread_id='other-chat')['update_goal']
            self.assertFalse(json.loads(tool.invoke({'state':'paused'}))['ok'])
            owner=self.build(goal_store=store,thread_id='owner-chat')['update_goal']
            self.assertTrue(json.loads(owner.invoke({'state':'paused'}))['ok'])

    def test_build_tools_exports_the_registered_chat_tools_in_stable_order(self):
        tools = self.build()

        self.assertEqual(
            list(tools),
            [
                "list_files",
                "read_file",
                "glob",
                "grep",
                "search_text",
                "edit_file",
                "write_file",
                "run_command",
                "task",
                "update_goal",
                "load_skill",
                "read_skill_resource",
                "memory_list",
                "memory_read",
                "read_tool_result",
            ],
        )

    def test_tool_result_is_json_and_preserves_chinese_text(self):
        (self.root / 'notes.txt').write_text('中文内容', encoding='utf-8')
        result = self.build()['read_file'].invoke({'path': 'notes.txt'})
        payload = json.loads(result)
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['content'], '中文内容')
        self.assertEqual(payload['status'], 'success')
        self.assertTrue(payload['started'])
        self.assertIn('中文内容', result)

    def test_all_tool_results_redact_the_configured_model_key(self):
        run_tool = self.build(approved=True, api_key='secret-sentinel')['run_command']
        result = run_tool.invoke({'command': "printf 'printed secret-sentinel'"})
        self.assertTrue(json.loads(result)['ok'])
        self.assertNotIn("secret-sentinel", result)
        self.assertIn("[密钥已隐藏]", result)

    def test_init_profile_only_exposes_context_write_target(self):
        tools = self.build(profile='init', approved=True)

        self.assertEqual(set(tools), {"list_files", "read_file", "write_file", "read_tool_result"})
        denied = json.loads(
            tools["write_file"].invoke(
                {"path": "README.md", "content": "wrong target"}
            )
        )
        self.assertFalse(denied["ok"])
        self.assertFalse(denied['started'])
        self.assertFalse((self.root / 'README.md').exists())
        self.assertIn(".nailong/context.md", denied["error"])

    def test_init_rejects_context_file_symlink_to_another_project_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            context_dir = root / ".nailong"
            context_dir.mkdir()
            readme = root / "README.md"
            readme.write_text("keep this file", encoding="utf-8")
            (context_dir / "context.md").symlink_to(readme)

            write_tool = self.build(profile='init', approved=True, file_session=FileSession(root))['write_file']
            result = json.loads(write_tool.invoke({'path': '.nailong/context.md', 'content': 'redirected write'}))

            self.assertFalse(result["ok"])
            self.assertFalse(result['started'])
            self.assertEqual(readme.read_text(encoding="utf-8"), "keep this file")

    def test_chat_write_rejects_protected_dotenv_symlink_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            readme = root / "README.md"
            readme.write_text("keep this file", encoding="utf-8")
            (root / ".env").symlink_to(readme)

            write_tool = self.build(approved=True, file_session=FileSession(root))['write_file']
            result = json.loads(write_tool.invoke({'path': '.env', 'content': 'overwrite'}))

            self.assertFalse(result["ok"])
            self.assertFalse(result['started'])
            self.assertEqual(readme.read_text(encoding="utf-8"), "keep this file")

    def test_review_profile_is_read_only_scoped_and_caps_unique_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for index in range(21):
                (root / f"file-{index:02}.py").write_text("print('ok')\n")

            tools = self.build(profile='review', target_path='.', file_session=FileSession(root))
            self.assertEqual(set(tools), {'list_files', 'read_file', 'read_tool_result'})
            listing = json.loads(tools['list_files'].invoke({'path': '.', 'limit': 100}))
            self.assertEqual(len(listing['files']), 20)
            self.assertTrue(listing['truncated'])
            for index in range(20):
                result = json.loads(tools['read_file'].invoke({'path': f'file-{index:02}.py'}))
                self.assertTrue(result['ok'])
            denied = json.loads(tools['read_file'].invoke({'path': 'file-20.py'}))
            self.assertFalse(denied['ok'])
            self.assertNotIn('content', denied)

    def test_review_profile_cannot_read_outside_the_requested_subdirectory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "review-me"
            outside = root / "other"
            target.mkdir()
            outside.mkdir()
            (target / "safe.py").write_text("pass\n")
            (outside / "private.py").write_text("secret\n")

            tools = self.build(profile='review', target_path='review-me', file_session=FileSession(root))
            result = json.loads(tools['read_file'].invoke({'path': 'other/private.py'}))

        self.assertFalse(result["ok"])
        self.assertNotIn('secret', str(result))
        self.assertIn("指定的审查目标范围", result["error"])

    def test_default_command_approval_is_required_and_zero_execution(self):
        command = "printf 'bad' > command-ran.txt"
        result = json.loads(self.build()['run_command'].invoke({'command': command}))
        self.assertEqual(result['error_code'], 'approval_required')
        self.assertFalse(result['started'])
        self.assertFalse((self.root / 'command-ran.txt').exists())

    def test_approved_existing_file_overwrite_requires_complete_read_first(self):
        target = self.root / 'existing.txt'
        target.write_text('keep this original')
        tools = self.build(approved=True)
        unread = json.loads(tools['write_file'].invoke({'path': 'existing.txt', 'content': 'bad'}))
        self.assertEqual(unread['error_code'], 'read_required')
        self.assertFalse(unread['started'])
        page = json.loads(tools['read_file'].invoke({'path': 'existing.txt', 'max_chars': 4}))
        self.assertFalse(page['read_complete'])
        partial = json.loads(tools['write_file'].invoke({'path': 'existing.txt', 'content': 'bad'}))
        self.assertEqual(partial['error_code'], 'read_required')
        self.assertEqual(target.read_text(), 'keep this original')
        full = json.loads(tools['read_file'].invoke({'path': 'existing.txt'}))
        self.assertTrue(full['read_complete'])
        saved = json.loads(tools['write_file'].invoke({'path': 'existing.txt', 'content': 'authorized replacement'}))
        self.assertTrue(saved['ok'])
        self.assertEqual(target.read_text(), 'authorized replacement')

    def test_default_builders_follow_context_project_in_parallel_threads(self):
        first = self.root / 'first-project'
        second = self.root / 'second-project'
        for root, content in ((first, 'first marker'), (second, 'second marker')):
            root.mkdir()
            (root / 'identity.txt').write_text(content)
        original_root = local_tools.selected_project_root()
        original_global = local_tools.PROJECT_ROOT
        ready = threading.Barrier(2)

        def consume(root, expected):
            with local_tools.use_project_root(root):
                tools = {tool.name: tool for tool in import_tools(self).build_tools()}
                specs = {spec.name: spec for spec in build_tool_specs()}
                tools['write_file'].execution.approval_handler = lambda *args: {'type': 'approve'}
                ready.wait(timeout=3)
                adapter_read = json.loads(tools['read_file'].invoke({'path': 'identity.txt'}))
                registry_read = specs['read_file'].handler(path='identity.txt')
                written = json.loads(tools['write_file'].invoke({'path': 'created.txt', 'content': expected}))
                return adapter_read, registry_read, written

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(consume, first, 'first marker'),
                       executor.submit(consume, second, 'second marker')]
            results = [future.result(timeout=5) for future in futures]
        for index, expected in enumerate(('first marker', 'second marker')):
            adapter_read, registry_read, written = results[index]
            self.assertTrue(adapter_read['ok'])
            self.assertEqual(adapter_read['content'], expected)
            self.assertTrue(registry_read['ok'])
            self.assertEqual(registry_read['content'], expected)
            self.assertTrue(written['ok'])
        self.assertEqual((first / 'created.txt').read_text(), 'first marker')
        self.assertEqual((second / 'created.txt').read_text(), 'second marker')
        self.assertFalse((self.root / 'created.txt').exists())
        self.assertEqual(local_tools.selected_project_root(), original_root)
        self.assertEqual(local_tools.PROJECT_ROOT, original_global)


if __name__ == "__main__":
    unittest.main()
