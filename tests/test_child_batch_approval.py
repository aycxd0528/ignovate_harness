"""Offline real AgentService -> task -> child graph capability isolation.

Only the chat provider is fake. The gate, parent and child graphs, native task
handler, task runner, permissions, ToolMessages and reads use production code.
The control selects the supported external callback integration path; it does
not replace a gate or handler. Fixtures live in a new temporary project.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import Field

from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.sessions import ProjectSessionStore


class RecordingOfflineModel(FakeMessagesListChatModel):
    seen_requests: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen_requests.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def reply(content='', calls=None):
    return AIMessage(content=content, tool_calls=calls or [],
                     usage_metadata={'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2})


class ParentChildPermitBoundaries(unittest.IsolatedAsyncioTestCase):
    async def run_actual_task(self, *, external=False):
        directory = tempfile.TemporaryDirectory(prefix='v4-real-parent-child-')
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        root = base / 'project'
        root.mkdir()
        (root / 'document.txt').write_text('native child evidence', encoding='utf-8')
        model = RecordingOfflineModel(responses=[
            reply(calls=[{'name': 'task', 'args': {'description': '读取 document.txt，返回实际证据。'}, 'id': 'parent-task'}]),
            reply(calls=[{'name': 'read_file', 'args': {'path': 'document.txt'}, 'id': 'child-read'}]),
            reply('Child returned after tool results'),
            reply('Parent returned after child'),
        ])
        settings = Settings('offline-key', 'https://example.invalid', 'deepseek-flash', root)
        with patch('agent.ChatDeepSeek', return_value=model):
            factory = AgentRuntimeFactory(settings,
                session_store=ProjectSessionStore(root, base_dir=base / 'private'))
        self.addCleanup(factory.close)
        self.addAsyncCleanup(factory.aclose)
        service = AgentService(factory, api_key='offline-key')
        if external:
            native = factory.tool_execution_context.approval_handler
            factory.tool_execution_context.approval_handler = lambda action, permission: native(action, permission)
        events = [event async for event in service.stream_turn(
            '请排查实现前的文件证据，用 task 只读研究 document.txt。',
            {'configurable': {'thread_id': 'parent-owner'}}, allowed_tools={'task'},
            approval_handler=lambda *args: 'approve_once')]
        child_messages = [m for request in model.seen_requests for m in request
                          if isinstance(m, ToolMessage) and m.tool_call_id == 'child-read']
        self.assertTrue(child_messages, 'The real child graph must request and receive its tool result')
        result = json.loads(child_messages[0].content)
        print(json.dumps({'external_control': external, 'child_read_result': result,
                          'final_event': events[-1].kind}, ensure_ascii=False))
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['started'], result)
        self.assertEqual(result['content'], 'native child evidence')

    async def test_native_parent_task_does_not_apply_parent_permit_to_child_read(self):
        await self.run_actual_task()

    async def test_external_callback_control_child_read_still_works(self):
        await self.run_actual_task(external=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
