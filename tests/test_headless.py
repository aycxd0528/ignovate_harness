from platform_fixtures import python_command, shell_join, editor_command
import asyncio
import io
import json
import tempfile
import unittest
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from config import Settings
from agent_service import TurnRecursionLimitError, TurnEvent
from agent import AgentRuntimeFactory
from nailong.core.goal import GoalStore
from nailong.core.sessions import ProjectSessionStore


class FakeService:
    def __init__(self, *_args, **_kwargs):
        self.call = None

    async def stream_turn(self, message, config, **kwargs):
        self.call = (message, config, kwargs)
        yield TurnEvent("status", {"status": "thinking"})
        yield TurnEvent("usage", {"input_tokens": 20, "output_tokens": 5, "cache_hit_tokens": 4, "total_tokens": 25})
        yield TurnEvent("tool_start", {"name": "read_file", "call_id": "read-1", "summary": "读取文件"})
        yield TurnEvent("tool_end", {"name": "read_file", "call_id": "read-1", "ok": True, "summary": "读取 10 个字符"})
        yield TurnEvent("final", {"text": "答案包含 private-key"})


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class HeadlessTests(unittest.TestCase):
    def make_settings(self, directory):
        return Settings("private-key", "https://api.deepseek.com", "unknown-model", Path(directory))

    def make_runtime(self):
        factory = SimpleNamespace(close=lambda: None, aclose=lambda: asyncio.sleep(0))
        return SimpleNamespace(_nailong_runtime_factory=factory)

    def run_permission_fixture(self, project, state_root, responses, *, permission_mode='default'):
        import agent
        import headless

        settings = Settings('private-key', 'https://api.deepseek.com', 'deepseek-chat', project)
        with patch.object(agent, 'ChatDeepSeek', return_value=ToolCallingFakeModel(responses=responses)):
            factory = AgentRuntimeFactory(settings,
                session_store=ProjectSessionStore(project, base_dir=state_root, api_key=settings.api_key))
            runtime = factory()
        stdout, stderr = io.StringIO(), io.StringIO()
        try:
            with patch.object(headless, 'create_agent_runtime', return_value=runtime):
                code = asyncio.run(headless.run_print(settings, '执行请求', output_format='json',
                    permission_mode=permission_mode, stdout=stdout, stderr=stderr))
        finally:
            factory.close()
        return code, json.loads(stdout.getvalue()), stdout.getvalue() + stderr.getvalue()

    def test_headless_bypass_executes_files_and_commands_and_does_not_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'project'
            project.mkdir()
            (project / '.nailong').mkdir()
            settings_path = project / '.nailong/settings.json'
            settings_path.write_text(json.dumps({'permissions': {'deny': ['Write(*)', 'Bash(*)']}}), newline='\n')
            before = settings_path.read_bytes()
            calls = [{'name': 'write_file', 'args': {'path': 'unattended.txt', 'content': 'created'}, 'id': 'write'},
                {'name': 'run_command', 'args': {'command': shell_join([sys.executable, '-B', '-c',
                    "from pathlib import Path;Path('command.txt').write_text('ran')"])}, 'id': 'command'}]
            code, payload, _ = self.run_permission_fixture(project, Path(directory) / 'state',
                [AIMessage(content='', tool_calls=calls), AIMessage(content='请求完成。')],
                permission_mode='bypassPermissions')
            self.assertEqual(code, 0)
            self.assertEqual(payload['status'], 'success')
            self.assertEqual(payload['approvals'], [])
            self.assertEqual((project / 'unattended.txt').read_text(), 'created')
            self.assertEqual((project / 'command.txt').read_text(), 'ran')
            self.assertEqual(settings_path.read_bytes(), before)
            (project / 'unattended.txt').unlink()
            code, payload, _ = self.run_permission_fixture(project, Path(directory) / 'state',
                [AIMessage(content='', tool_calls=calls[:1]), AIMessage(content='写入被拒绝。')])
            self.assertFalse((project / 'unattended.txt').exists())
            self.assertEqual(settings_path.read_bytes(), before)
            # The next default process still obeys the unchanged deny rule.
            self.assertTrue(any(tool['status'] == 'failed' for tool in payload['tools']))

    def test_headless_bypass_preserves_secret_rejection_and_output_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'project'
            project.mkdir()
            calls = [{'name': 'write_file', 'args': {'path': 'secret-copy.txt', 'content': 'private-key'},
                'id': 'secret'}]
            _, payload, rendered = self.run_permission_fixture(project, Path(directory) / 'state',
                [AIMessage(content='', tool_calls=calls), AIMessage(content='已拒绝 private-key。')],
                permission_mode='bypassPermissions')
            self.assertFalse((project / 'secret-copy.txt').exists())
            self.assertTrue(any(tool['status'] == 'failed' for tool in payload['tools']))
            self.assertNotIn('private-key', rendered)
            self.assertIn('[密钥已隐藏]', rendered)
            self.assertEqual(payload['approvals'], [])

    def test_headless_full_access_writes_external_and_protected_files_only_when_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / 'project'
            project.mkdir()
            outside = Path(directory) / 'absolute-outside.txt'
            paths = [str(outside), '../relative-outside.txt', '.env']
            calls = [{'name': 'write_file', 'args': {'path': path, 'content': 'full access'},
                'id': f'write-{index}'} for index, path in enumerate(paths)]
            code, payload, _ = self.run_permission_fixture(project, Path(directory) / 'state',
                [AIMessage(content='', tool_calls=calls), AIMessage(content='请求完成。')],
                permission_mode='bypassPermissions')
            self.assertEqual(code, 0)
            self.assertEqual(payload['approvals'], [])
            targets = [outside, Path(directory) / 'relative-outside.txt', project / '.env']
            self.assertEqual([path.read_text() for path in targets], ['full access'] * 3)
            for call in calls:
                call['args']['content'] = 'default must not overwrite'
            _, payload, _ = self.run_permission_fixture(project, Path(directory) / 'state',
                [AIMessage(content='', tool_calls=calls), AIMessage(content='拒绝越界写入。')])
            self.assertEqual([path.read_text() for path in targets], ['full access'] * 3)
            self.assertEqual([tool['status'] for tool in payload['tools']], ['failed'] * 3)

    def test_headless_rejects_unsupported_permission_mode_before_starting_runtime(self):
        import headless

        with tempfile.TemporaryDirectory() as directory, patch.object(headless, 'create_agent_runtime') as create:
            stderr = io.StringIO()
            code = asyncio.run(headless.run_print(self.make_settings(directory), '请求', permission_mode='plan',
                stdout=io.StringIO(), stderr=stderr))
            self.assertEqual(code, 3)
            self.assertIn('权限模式', stderr.getvalue())
            create.assert_not_called()

    def test_json_format_contains_result_usage_tools_session_and_redacts_secret(self):
        import headless

        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            stderr = io.StringIO()
            service = FakeService()
            with (
                patch.object(headless, "create_agent_runtime", return_value=self.make_runtime()),
                patch.object(headless, "AgentService", return_value=service),
            ):
                code = asyncio.run(
                    headless.run_print(
                        self.make_settings(directory),
                        "检查项目",
                        output_format="json",
                        max_turns=12,
                        stdout=stdout,
                        stderr=stderr,
                    )
                )

        result = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result["result"], "答案包含 [密钥已隐藏]")
        self.assertEqual(result["usage"]["total_tokens"], 25)
        self.assertEqual(result["tools"][0]["name"], "read_file")
        self.assertTrue(result["session_id"])
        self.assertEqual(service.call[2]["max_model_calls"], 12)
        self.assertIsNone(service.call[2]["approval_handler"])
        self.assertNotIn("private-key", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_text_format_prints_only_final_answer_and_stream_json_is_line_delimited(self):
        import headless

        with tempfile.TemporaryDirectory() as directory:
            settings = self.make_settings(directory)
            runtime = self.make_runtime()
            with (
                patch.object(headless, "create_agent_runtime", return_value=runtime),
                patch.object(headless, "AgentService", return_value=FakeService()),
            ):
                text = io.StringIO()
                asyncio.run(headless.run_print(settings, "问题", stdout=text, stderr=io.StringIO()))
                stream = io.StringIO()
                asyncio.run(
                    headless.run_print(
                        settings,
                        "问题",
                        output_format="stream-json",
                        stdout=stream,
                        stderr=io.StringIO(),
                    )
                )

        self.assertEqual(text.getvalue(), "答案包含 [密钥已隐藏]\n")
        lines = stream.getvalue().splitlines()
        decoded = [json.loads(line) for line in lines]
        self.assertEqual(decoded[0]["type"], "status")
        self.assertEqual(decoded[-1]["type"], "headless_result")
        self.assertTrue(all(isinstance(item, dict) for item in decoded))

    def test_graph_limit_returns_exit_code_two_and_structured_error(self):
        import headless

        class LimitService:
            def __init__(self, *_args, **_kwargs):
                pass

            async def stream_turn(self, *_args, **_kwargs):
                raise TurnRecursionLimitError("turn limit")
                yield

        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            with (
                patch.object(headless, "create_agent_runtime", return_value=self.make_runtime()),
                patch.object(headless, "AgentService", LimitService),
            ):
                code = asyncio.run(
                    headless.run_print(
                        self.make_settings(directory), "问题", output_format="json", stdout=stdout, stderr=io.StringIO()
                    )
                )

        result = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "turn_limit")

    def test_headless_approval_request_is_rejected_without_writing_file(self):
        import agent
        import headless

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            state_root = Path(directory) / "state"
            settings = Settings("private-key", "https://api.deepseek.com", "deepseek-chat", project)
            model = ToolCallingFakeModel(responses=[
                AIMessage(content="", tool_calls=[{
                    "name": "write_file",
                    "args": {"path": "unattended.txt", "content": "must not run"},
                    "id": "unattended-write",
                }]),
                AIMessage(content="写入被拒绝。"),
            ])
            with patch.object(agent, "ChatDeepSeek", return_value=model):
                factory = AgentRuntimeFactory(
                    settings,
                    session_store=ProjectSessionStore(project, base_dir=state_root, api_key="private-key"),
                )
                runtime = factory()
            stdout = io.StringIO()
            with patch.object(headless, "create_agent_runtime", return_value=runtime):
                code = asyncio.run(
                    headless.run_print(
                        settings,
                        "创建文件",
                        output_format="json",
                        stdout=stdout,
                        stderr=io.StringIO(),
                    )
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "approval_required")
        self.assertFalse((project / "unattended.txt").exists())
        self.assertEqual(len(payload["approvals"]), 1)

    def test_unattended_goal_pauses_at_approval_and_can_be_resumed_interactively(self):
        import headless

        class ApprovalService:
            calls = 0

            def __init__(self, *_args, **_kwargs):
                pass

            async def stream_turn(self, message, config, **kwargs):
                self.calls += 1
                yield TurnEvent("usage", {"input_tokens": 100, "output_tokens": 20, "cache_hit_tokens": 0, "total_tokens": 120})
                yield TurnEvent("approval_needed", {"actions": [{"name": "write_file", "reason": "需要审批"}]})
                yield TurnEvent("approval_decision", {"name": "write_file", "kind": "reject"})
                yield TurnEvent("final", {"text": "需要用户确认，目标暂停。"})

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".nailong").mkdir()
            (root / ".nailong" / "settings.json").write_text(json.dumps({
                "pricing": {"unknown-model": {
                    "input_per_million": 1,
                    "cache_hit_per_million": 0.2,
                    "output_per_million": 2,
                }}
            }), encoding="utf-8", newline='\n')
            settings = self.make_settings(directory)
            store = GoalStore(root / ".nailong" / "goals.json", api_key="private-key")
            factory = SimpleNamespace(
                goal_store=store,
                task_runner=None,
                close=lambda: None,
                aclose=lambda: asyncio.sleep(0),
            )
            runtime = SimpleNamespace(_nailong_runtime_factory=factory)
            service = ApprovalService()
            stdout = io.StringIO()
            with (
                patch.object(headless, "create_agent_runtime", return_value=runtime),
                patch.object(headless, "AgentService", return_value=service),
            ):
                code = asyncio.run(headless.run_print(
                    settings,
                    "修复并验证",
                    output_format="json",
                    goal_mode=True,
                    goal_max_rounds=5,
                    goal_max_cost_usd=0.25,
                    stdout=stdout,
                    stderr=io.StringIO(),
                ))

            payload = json.loads(stdout.getvalue())
            paused = store.latest()
            resumed, _message, resumed_goal = store.resume(paused.id, thread_id="interactive-thread")

        self.assertEqual(code, 1)
        self.assertEqual(service.calls, 1)
        self.assertEqual(payload["status"], "approval_required")
        self.assertEqual(payload["goal"]["state"], "paused")
        self.assertIn("审批", payload["goal"]["pause_reason"])
        self.assertTrue(resumed)
        self.assertEqual(resumed_goal.thread_id, "interactive-thread")
