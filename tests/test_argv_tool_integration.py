import json
import sys
import tempfile
import unittest
from pathlib import Path
from tools import build_tools
from nailong.core.permissions import PermissionEngine
from nailong.tools.execution import ToolExecutionContext
from nailong.tools.files import FileSession

class ArgvToolIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.session=FileSession(self.root)
        self.approvals=[]
        self.engine=PermissionEngine(self.root,rules={})
        self.execution=ToolExecutionContext(self.root,permission_engine=self.engine,
            artifact_directory=self.root/'private',approval_handler=lambda a,p:self.approvals.append(a) or {'type':'approve'})
        self.tools={t.name:t for t in build_tools(file_session=self.session,execution_context=self.execution)}
    async def call(self,args):
        message=await self.tools['run_command'].ainvoke({'type':'tool_call','name':'run_command','id':'one','args':args},
            config={'configurable':{'thread_id':'one'}})
        return json.loads(message.content)
    async def test_argv_reaches_real_process_and_archive(self):
        result=await self.call({'argv':[sys.executable,'-c','import os,sys;print(os.getcwd());sys.exit(7)'], 'expected_exit_code':7})
        self.assertTrue(result['ok']);self.assertEqual(result['exit_code'],7)
        self.assertEqual(result['execution_mode'],'argv');self.assertFalse(result['shell'])
        self.assertIn(str(self.root),result['output'])
        self.assertIn('output_archive',result)
        self.assertEqual(self.approvals[0]['args']['argv'][0],sys.executable)
        self.assertEqual(self.approvals[0]['args']['command'],'')
    async def test_argv_deny_prevents_marker_and_approval(self):
        self.engine.add_rule('deny','Bash(*)')
        result=await self.call({'argv':[sys.executable,'-c',"open('marker','w').write('bad')"]})
        self.assertFalse(result['started']);self.assertFalse((self.root/'marker').exists());self.assertEqual(self.approvals,[])

    async def test_suggested_argv_session_grant_starts_real_process_without_approval(self):
        # Break caught: shell parsing of literal code backslashes makes an exact argv grant unusable.
        args = {'argv': [sys.executable, '-c', "print('first\\nsecond')"]}
        self.engine.grant_session(self.engine.suggest_rule('run_command', args))
        result = await self.call(args)
        self.assertTrue(result['started'], result)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['output'].splitlines(), ['first', 'second'])
        self.assertEqual(self.approvals, [])
    async def test_dual_input_never_starts(self):
        result=await self.call({'command':'echo yes','argv':[sys.executable,'-c','pass']})
        self.assertFalse(result['started']);self.assertFalse(result['ok']);self.assertEqual(self.approvals,[])

    async def test_escaped_output_does_not_remove_actual_exit_or_archive_identity(self):
        result=await self.call({'argv':[sys.executable,'-c',"print('\\\\'*10000)"], 'expected_exit_code':0})
        self.assertTrue(result['ok']);self.assertEqual(result['exit_code'],0)
        self.assertEqual(result['expected_exit_code'],0)
        self.assertTrue(result['completed']);self.assertEqual(result['execution_mode'],'argv')
        self.assertIn('reference',result['output_archive'])

if __name__=='__main__':unittest.main()
