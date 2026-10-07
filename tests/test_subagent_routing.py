"""Task tools are unavailable for simple introductions, with explicit opt-in preserved."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage
from agent_service import AgentService
from nailong.core.permissions import PermissionEngine
from nailong.core.task_requests import is_simple_project_question, task_request_kind


class SubagentRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_intro_excludes_task_even_when_caller_exposes_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = SimpleNamespace(
                get_state=lambda _: SimpleNamespace(values={'messages': []}, metadata={'step': 0}),
                ainvoke=AsyncMock(return_value={'messages': [AIMessage(content='项目简介')]}),
            )
            factory = SimpleNamespace(settings=SimpleNamespace(project_root=root),
                async_runtime=AsyncMock(return_value=agent))
            service = AgentService(factory, permission_engine=PermissionEngine(root))
            await service.run_turn('介绍一下这个项目的上下文处理', {'configurable': {'thread_id': 'intro'}},
                allowed_tools={'read_file', 'task', 'edit_file', 'run_command'}, history_display='介绍一下这个项目的上下文处理')
            self.assertEqual(factory.async_runtime.call_args.kwargs['allowed_tools'], {'read_file'})

    async def test_default_intro_uses_existing_core_tools_without_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = SimpleNamespace(get_state=lambda _: SimpleNamespace(values={'messages': []}, metadata={'step': 0}),
                ainvoke=AsyncMock(return_value={'messages': [AIMessage(content='简介')]}))
            factory = SimpleNamespace(settings=SimpleNamespace(project_root=root),
                async_runtime=AsyncMock(return_value=agent))
            service = AgentService(factory, permission_engine=PermissionEngine(root))
            await service.run_turn('介绍一下这个项目', {'configurable': {'thread_id': 'intro'}})
            exposed = factory.async_runtime.call_args.kwargs['allowed_tools']
            self.assertIn('read_file', exposed)
            self.assertNotIn('task', exposed)
            self.assertNotIn('edit_file', exposed)
            self.assertNotIn('run_command', exposed)

    def test_explicit_delegation_and_complex_tasks_are_not_suppressed(self):
        for request in ('让子代理介绍一下这个项目', '介绍项目，分别分析多个模块',
                '审查项目权限并修改实现', '介绍项目，使用 subagent'):
            with self.subTest(request=request):
                self.assertFalse(is_simple_project_question(request))
        self.assertTrue(is_simple_project_question('介绍一下这个项目的上下文处理'))

    def test_questions_are_not_work_and_mixed_request_remains_work(self):
        self.assertEqual(task_request_kind('修改方案是什么'), 'other')
        self.assertEqual(task_request_kind('测试为什么失败'), 'other')
        self.assertEqual(task_request_kind('进度怎么样，接下来请修复 source.py'), 'work')
        self.assertEqual(task_request_kind('请修改 source.py，不要运行测试'), 'work')
        self.assertEqual(task_request_kind('不要恢复任务，修改方案先解释一下'), 'other')
        self.assertEqual(task_request_kind('请修复 source.py，完成后只说明修改结果'), 'work')
