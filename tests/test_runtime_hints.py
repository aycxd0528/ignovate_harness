"""Request facts help the model choose tools without inventing capabilities."""
import json
import sys
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

class RecordingModel(FakeMessagesListChatModel):
    requests:list=[]
    def bind_tools(self,tools,**kwargs):return self
    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        self.requests.append(list(messages))
        return super()._generate(messages,stop=stop,run_manager=run_manager,**kwargs)

class RuntimeHintTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_request_has_interpreter_temp_and_no_bound_goal(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)/'project';root.mkdir()
        store=ProjectSessionStore(root,base_dir=Path(temp.name)/'private')
        model=RecordingModel(responses=[AIMessage(content='ready')])
        with patch('agent.ChatDeepSeek',return_value=model):
            factory=AgentRuntimeFactory(Settings('key','https://example.invalid','private',root),session_store=store)
        self.addCleanup(factory.close);self.addAsyncCleanup(factory.aclose)
        events=[e async for e in AgentService(factory).stream_turn('介绍当前项目',{'configurable':{'thread_id':'one'}})]
        report=factory.context_reports['one']
        self.assertEqual(report['runtime_environment']['python_executable'],sys.executable)
        self.assertEqual(report['runtime_environment']['temporary_directory'],tempfile.gettempdir())
        self.assertFalse(report['runtime_environment']['active_goal_bound'])
        body='\n'.join(str(m.content) for m in model.requests[-1])
        self.assertIn(json.dumps(sys.executable),body)
        self.assertIn('active_goal_bound',body)
        self.assertEqual(events[-1].kind,'final')

if __name__=='__main__':unittest.main()
