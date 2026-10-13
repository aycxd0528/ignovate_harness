import json
import sys
import tempfile
import unittest
from pathlib import Path
from tools import build_tools
from nailong.core.sessions import ProjectSessionStore
from nailong.core.task_state import TaskStore
from nailong.tools.execution import ToolExecutionContext
from nailong.tools.files import FileSession

class StageExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();(self.root/'smoke_alpha.py').write_text("open('marker','w').write('ran')")
        self.tasks=TaskStore(ProjectSessionStore(self.root,base_dir=self.root/'private'))
        self.approvals=[]
        self.execution=ToolExecutionContext(self.root,approval_handler=self.approve)
        self.execution.task_snapshot_provider=self.tasks.snapshot
        self.tools={t.name:t for t in build_tools(file_session=FileSession(self.root),execution_context=self.execution)}
    def approve(self,action,permission):
        self.approvals.append(action)
        if getattr(self,'change_stage',False):self.tasks.begin('one','Current stage: Review.')
        return {'type':'approve'}
    async def call(self):
        message=await self.tools['run_command'].ainvoke({'type':'tool_call','name':'run_command','id':'exec',
            'args':{'argv':[sys.executable,'smoke_alpha.py']}},config={'configurable':{'thread_id':'one'}})
        return json.loads(message.content)
    async def test_early_stage_blocks_actual_process_before_approval(self):
        self.tasks.begin('one','Current stage: Review. Only run smoke_alpha.py during stage Deliver.')
        body=await self.call()
        self.assertFalse((self.root/'marker').exists());self.assertEqual(self.approvals,[])
        self.assertEqual(body['error_code'],'stage_restricted');self.assertFalse(body['started'])
    async def test_stage_change_during_approval_is_rechecked(self):
        self.tasks.begin('one','Current stage: Deliver. Only run smoke_alpha.py during stage Deliver.')
        self.change_stage=True
        body=await self.call()
        self.assertFalse((self.root/'marker').exists());self.assertEqual(len(self.approvals),1)
        self.assertEqual(body['error_code'],'stage_restricted')
    async def test_matching_stage_allows_real_process(self):
        self.tasks.begin('one','Current stage: Deliver. Only run smoke_alpha.py during stage Deliver.')
        body=await self.call()
        self.assertTrue(body['ok']);self.assertEqual((self.root/'marker').read_text(),'ran')

if __name__=='__main__':unittest.main()
