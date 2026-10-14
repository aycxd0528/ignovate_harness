import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from agent import AgentRuntimeFactory
from agent_service import AgentService
from config import Settings
from nailong.core.sessions import ProjectSessionStore

class OfflineModel(FakeMessagesListChatModel):
    def bind_tools(self,tools,**kwargs):return self

class ToolResultIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_reused_native_call_id_records_both_written_files(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)/'project';root.mkdir()
        store=ProjectSessionStore(root,base_dir=Path(temp.name)/'private')
        model=OfflineModel(responses=[AIMessage(content='',tool_calls=[{'name':'write_file','id':'same','args':{'path':'a.txt','content':'a'}}]),
            AIMessage(content='First result.'),AIMessage(content='',tool_calls=[{'name':'write_file','id':'same','args':{'path':'b.txt','content':'b'}}]),AIMessage(content='Second result.')])
        with patch('agent.ChatDeepSeek',return_value=model):
            factory=AgentRuntimeFactory(Settings('key','https://example.invalid','private',root),session_store=store)
        self.addCleanup(factory.close);self.addAsyncCleanup(factory.aclose)
        service=AgentService(factory);config={'configurable':{'thread_id':'one'}}
        approve=lambda *args:'approve_once'
        first=[e async for e in service.stream_turn('创建 a.txt 文件',config,approval_handler=approve)]
        second=[e async for e in service.stream_turn('继续创建 b.txt 文件',config,approval_handler=approve)]
        task=factory.task_store.snapshot('one')
        self.assertEqual((root/'a.txt').read_text(),'a');self.assertEqual((root/'b.txt').read_text(),'b')
        self.assertEqual(set(task['changed_paths']),{'a.txt','b.txt'})
        edits=[r for r in task['evidence'] if r['kind']=='edit']
        self.assertEqual(len(edits),2);self.assertEqual(len({r['id'] for r in edits}),2)
        self.assertEqual(len([e for e in first+second if e.kind=='tool_end']),2)

    async def test_same_turn_reused_call_tracks_each_fresh_progress_observation_once(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)/'project';root.mkdir();(root/'config.txt').write_text('value=1')
        store=ProjectSessionStore(root,base_dir=Path(temp.name)/'private')
        calls=[AIMessage(content='',tool_calls=[{'name':'read_file','id':'same','args':{'path':'config.txt'}}]) for _ in range(2)]
        model=OfflineModel(responses=[*calls,AIMessage(content='Read facts.')])
        with patch('agent.ChatDeepSeek',return_value=model):
            factory=AgentRuntimeFactory(Settings('key','https://example.invalid','private',root),session_store=store)
        self.addCleanup(factory.close);self.addAsyncCleanup(factory.aclose)
        service=AgentService(factory)
        events=[e async for e in service.stream_turn('修复 config.txt 配置问题，先读取配置文件',{'configurable':{'thread_id':'one'}})]
        task=factory.task_store.snapshot('one')
        self.assertEqual(len([e for e in events if e.kind=='tool_end']),2)
        self.assertEqual(len(task['progress_observations']),2)
        self.assertEqual(len([r for r in task['evidence'] if r['kind']=='read']),2)

if __name__=='__main__':unittest.main()
