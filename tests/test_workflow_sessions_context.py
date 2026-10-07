import importlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from langchain_core.messages import AIMessage,HumanMessage,ToolMessage
from nailong.core.sessions import ProjectSessionStore

class SessionsContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.project=tempfile.TemporaryDirectory(); self.data=tempfile.TemporaryDirectory()
        self.addCleanup(self.project.cleanup); self.addCleanup(self.data.cleanup)
        self.root=Path(self.project.name).resolve()
        self.store=ProjectSessionStore(self.root,base_dir=self.data.name,api_key='private-key')
        self.store.append_event('one','user',{'text':'fix auth bug'})
        self.store.append_event('one','final',{'text':'fixed but not tested'})
        self.store.append_event('two','user',{'text':'read files'})
    def actions(self):
        try: cls=importlib.import_module('nailong.core.session_actions').SessionActions
        except ModuleNotFoundError: self.fail('SessionActions missing')
        return cls(self.root,self.store,api_key='private-key')
    async def test_durable_name_search_and_filtered_selection(self):
        self.assertTrue(hasattr(self.store,'rename'),'Durable session rename missing')
        self.store.rename('one','Auth work')
        restored=ProjectSessionStore(self.root,base_dir=self.data.name)
        self.assertEqual(restored.search('auth')[0]['name'],'Auth work')
        self.assertEqual(restored.resolve_session('1',query='auth'),'one')
        self.assertEqual(restored.rename('one','bad\nname'),'badname')
        for name in ['', 'x'*81]:
            with self.assertRaises(ValueError): restored.rename('one',name)
        (restored.root/'session-metadata.json').write_text('{bad')
        self.assertEqual(len(restored.list_sessions()),2)

    async def test_filtered_indices_remain_bound_to_displayed_snapshot(self):
        self.store.rename('one','match'); self.store.rename('two','match')
        shown=self.store.search('match')
        self.store.append_event(shown[-1]['thread_id'],'final',{'text':'new timestamp'})
        self.assertEqual(self.store.resolve_session('1',query='match'),shown[0]['thread_id'])
    async def test_safe_recap_export_and_overwrite_rejection(self):
        self.store.append_event('one','tool_end',{'name':'read_file','summary':'safe','raw':'private-key'})
        self.store.append_event('one','verification',{'status':'partial','steps':[{'name':'test','ok':True,'command':'secret command'}]})
        recap=self.actions().recap('one')
        self.assertIn('partial',recap); self.assertIn('未验证',recap)
        result=await self.actions().export('one')
        path=Path(result['path']); content=path.read_text()
        self.assertIn('fix auth bug',content); self.assertNotIn('private-key',content); self.assertNotIn('secret command',content)
        async def reject(*args): return 'reject'
        path.write_text('keep')
        result=await self.actions().export('one',str(path),approval=reject)
        self.assertFalse(result['written']); self.assertEqual(path.read_text(),'keep')
        with self.assertRaises(ValueError): await self.actions().export('one','../escape.md')
        with self.assertRaises(ValueError): await self.actions().export('one','.env')
    async def test_context_categories_are_mutually_exclusive_and_sum(self):
        try: report=importlib.import_module('nailong.core.context').context_report
        except ModuleNotFoundError: self.fail('Request context report missing')
        messages=[HumanMessage(content='hello'),AIMessage(content='',tool_calls=[{'name':'load_skill','id':'skill','args':{}}]),
                  ToolMessage(content='skill text',tool_call_id='skill',name='load_skill'),
                  ToolMessage(content='file text',tool_call_id='file',name='read_file'),
                  HumanMessage(content='plan',additional_kwargs={'nailong_pin':'plan'})]
        result=report({'base_system':'system','fixed_memory':'memory','skill_catalog':'catalog','tool_definitions':'schema'},messages)
        self.assertEqual(result['estimated_tokens'],sum(row['tokens'] for row in result['categories'].values()))
        self.assertEqual(result['categories']['loaded_skills']['characters'],len('skill text'))
        self.assertEqual(result['categories']['tool_results']['characters'],len('file text'))
        self.assertGreater(result['categories']['history']['characters'],len('hello'))
        self.assertIn('估算',result['method'])
