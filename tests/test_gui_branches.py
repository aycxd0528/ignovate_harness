import copy
import multiprocessing
import tempfile
import unittest
from pathlib import Path

from nailong.core.sessions import ProjectSessionStore


def attempt_lock(project, base, output):
    from nailong.core.project_execution import project_execution
    try:
        with project_execution(ProjectSessionStore(project, base_dir=base)):
            output.put('acquired')
    except BlockingIOError:
        output.put('busy')


class BranchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'project'
        self.project.mkdir()
        self.sessions = ProjectSessionStore(self.project, base_dir=Path(self.temp.name) / 'data')

    def store(self):
        from nailong.core.branches import BranchStore
        store = BranchStore(self.sessions)
        self.addCleanup(store.close)
        return store

    def add_turn(self, store, branch, prompt, answer, checkpoint='cp-1', usage=12):
        start = len(self.sessions.read_events(branch['thread_id']))
        self.sessions.append_event(branch['thread_id'], 'user', {'text': prompt})
        self.sessions.append_event(branch['thread_id'], 'tool_start', {'id': 'call1', 'name': 'read_file'})
        self.sessions.append_event(branch['thread_id'], 'tool_end', {'id': 'call1', 'name': 'read_file', 'ok': False})
        self.sessions.append_event(branch['thread_id'], 'usage', {'input_tokens': usage, 'output_tokens': 2})
        self.sessions.append_event(branch['thread_id'], 'final', {'text': answer})
        return store.commit_turn(branch['id'], start, checkpoint, task_snapshot=None)

    def test_paths_share_ancestors_without_copying_later_turns(self):
        store = self.store()
        root = store.create('主分支')
        first = self.add_turn(store, root, '共同要求', '共同结论')
        self.add_turn(store, root, 'A 独有要求', 'A 结论', 'cp-2')
        child = store.create_fork(root['id'], first['id'], '方案 B', 'fork-cp')
        self.add_turn(store, child, 'B 独有要求', 'B 结论', 'cp-b')
        path = store.path(child['id'])
        self.assertEqual([n['prompt'] for n in path], ['共同要求', 'B 独有要求'])
        self.assertEqual(path[0]['id'], first['id'])
        self.assertEqual(len(self.sessions.read_events(child['thread_id'])), 5)
        self.assertEqual(store.usage(root['conversation_id'])['input_tokens'], 36)
        self.assertFalse(path[0]['tools'][0]['ok'])

    def test_first_user_input_names_conversation_once_and_manual_titles_win(self):
        store=self.store();root=store.create()
        store.title_from_input(root['id'],'  请帮我\n  修复登录问题  ')
        self.assertEqual(store.branch(root['id'])['name'],'请帮我 修复登录问题')
        store.title_from_input(root['id'],'第二个问题')
        self.assertEqual(store.branch(root['id'])['name'],'请帮我 修复登录问题')
        custom=store.create();store.update_branch(custom['id'],name='新会话')
        store.title_from_input(custom['id'],'不能覆盖手动标题')
        self.assertEqual(store.branch(custom['id'])['name'],'新会话')
        first=self.add_turn(store,root,'共同问题','模型输出不能成为标题')
        child=store.create_fork(root['id'],first['id'],'备选方案','fork-cp')
        store.title_from_input(child['id'],'子分支后续输入')
        self.assertEqual(store.branch(child['id'])['name'],'备选方案')

    def test_imported_unnamed_conversation_uses_first_user_instead_of_final(self):
        self.sessions.append_event('legacy','user',{'text':'介绍项目结构'})
        self.sessions.append_event('legacy','final',{'text':'我是模型，以下是回答'})
        store=self.store();store.import_sessions()
        self.assertEqual(store.branches()[0]['name'],'介绍项目结构')

    def test_turn_timing_and_artifacts_derive_from_recorded_events(self):
        from nailong.core.branches import project_turn
        from nailong.gui.presentation import artifact_cards
        node={'id':'n','thread_id':'t','status':'completed','checkpoint_id':'cp'}
        events=[
            {'kind':'user','timestamp':'2026-10-12T12:00:00+00:00','data':{'text':'创建报告'}},
            {'kind':'tool_start','data':{'name':'write_file','call_id':'a','preview':{'path':'report.md'}}},
            {'kind':'tool_end','data':{'name':'write_file','call_id':'a','ok':True}},
            {'kind':'tool_start','data':{'name':'read_file','call_id':'b','preview':{'path':'README.md'}}},
            {'kind':'tool_end','data':{'name':'read_file','call_id':'b','ok':True}},
            {'kind':'final','timestamp':'2026-10-12T12:00:05+00:00','data':{'text':'已创建报告'}},
        ]
        turn=project_turn(node,events)
        self.assertEqual(turn['timing']['elapsed_ms'],5000)
        artifacts=artifact_cards(turn)
        self.assertEqual([item['path'] for item in artifacts if item['kind']=='file'],['report.md'])
        self.assertNotIn('README.md',str(artifacts))

    def test_layout_revision_conflict_does_not_touch_history(self):
        store = self.store()
        root = store.create('根')
        self.add_turn(store, root, '问题', '回答')
        saved = store.save_layout({'positions': {}, 'collapsed': [], 'viewport': {'x': 10, 'y': 20, 'zoom': 1}}, 0)
        with self.assertRaisesRegex(ValueError, '版本'):
            store.save_layout({'positions': {}}, 0)
        self.assertEqual(store.layout()['revision'], saved['revision'])
        self.assertEqual(len(store.path(root['id'])), 1)
        reopened = self.store()
        self.assertEqual(reopened.layout()['viewport']['x'], 10)

    def test_incomplete_or_missing_checkpoint_cannot_fork(self):
        store = self.store()
        root = store.create('根')
        node = self.add_turn(store, root, '旧问题', '回答', checkpoint=None)
        with self.assertRaisesRegex(ValueError, '恢复'):
            store.create_fork(root['id'], node['id'], '错误分叉', 'cp')
        self.assertEqual(len(store.branches()), 1)

    def test_project_lock_contends_across_processes(self):
        from nailong.core.project_execution import project_execution
        ctx = multiprocessing.get_context('spawn')
        output = ctx.Queue()
        with project_execution(self.sessions):
            process = ctx.Process(target=attempt_lock, args=(str(self.project), str(self.sessions.root.parent.parent), output))
            process.start()
            self.assertEqual(output.get(timeout=10), 'busy')
            process.join(10)
        self.assertEqual(process.exitcode, 0)

    def test_execution_lease_allows_nested_same_owner(self):
        from nailong.core.project_execution import project_execution
        with project_execution(self.sessions):
            with project_execution(self.sessions):
                self.assertTrue(True)

    def test_checkpoint_clone_excludes_later_messages_and_open_tools(self):
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.checkpoint.base import empty_checkpoint
        from langchain_core.messages import AIMessage, HumanMessage
        from nailong.core.branches import clone_checkpoint
        self.sessions.ensure_directories()
        with SqliteSaver.from_conn_string(str(self.sessions.database_path)) as saver:
            saver.setup()
            checkpoint = empty_checkpoint()
            checkpoint['channel_values'] = {'messages': [HumanMessage('common'), AIMessage('answer')]}
            checkpoint['channel_versions'] = {'messages': '1'}
            origin = saver.put({'configurable': {'thread_id': 'a', 'checkpoint_ns': ''}}, checkpoint, {'source': 'loop', 'step': 1}, {'messages': '1'})
            reference = clone_checkpoint(saver, 'a', checkpoint['id'], 'b')
            cloned = saver.get_tuple({'configurable': {'thread_id': 'b', 'checkpoint_id': reference}})
            self.assertEqual([m.content for m in cloned.checkpoint['channel_values']['messages']], ['common', 'answer'])
            broken = copy.deepcopy(checkpoint)
            broken.update(id=empty_checkpoint()['id'])
            broken['channel_values']['messages'].append(AIMessage('', tool_calls=[{'name': 'read_file', 'args': {}, 'id': 'open'}]))
            saver.put(origin, broken, {'source': 'loop', 'step': 2}, {})
            with self.assertRaisesRegex(ValueError, '工具'):
                clone_checkpoint(saver, 'a', broken['id'], 'bad')

    def test_usage_uses_saved_call_prices_and_never_reprices_history(self):
        store = self.store()
        branch = store.create('费用')
        self.sessions.append_event(branch['thread_id'], 'user', {'text': '问题'})
        self.sessions.append_event(branch['thread_id'], 'usage', {'input_tokens': 100, 'output_tokens': 50,
            'cache_hit_tokens': 20, 'price_snapshot': {'input_per_million': 2, 'cache_hit_per_million': 1, 'output_per_million': 4}})
        self.sessions.append_event(branch['thread_id'], 'final', {'text': '回答'})
        node = store.commit_turn(branch['id'], 0, 'cp')
        self.assertAlmostEqual(node['usage']['cost_usd'], .00038)

    def test_cli_append_is_visible_and_rewind_cannot_change_inherited_history(self):
        store = self.store()
        root = store.create('CLI compatible')
        first = self.add_turn(store, root, 'shared', 'original')
        child = store.create_fork(root['id'], first['id'], 'B', 'cp')
        self.sessions.append_event(root['thread_id'], 'user', {'text': 'CLI later'})
        self.sessions.append_event(root['thread_id'], 'final', {'text': 'CLI answer'})
        store.import_sessions()
        self.assertEqual([n['prompt'] for n in store.path(root['id'])], ['shared', 'CLI later'])
        self.sessions.truncate_events(root['thread_id'], 0)
        store.import_sessions()
        self.assertEqual(store.path(child['id'])[0]['answer'], 'original')
        self.assertTrue(store.branch(root['id'])['history_conflict'])

    def test_fork_copies_only_reachable_archive_records(self):
        from nailong.core.branches import ForkHistory
        from nailong.core.history_archive import HistoryArchive
        from langchain_core.messages import HumanMessage, ToolMessage
        archive = HistoryArchive(self.sessions.root / 'history-results')
        shared = archive.save('a', ToolMessage('full evidence', tool_call_id='call'))
        future = archive.save('a', HumanMessage('A later secret'))
        index = archive.save('a', HumanMessage('index: '+shared))
        mapping = ForkHistory(archive, 'a', 'b')
        copied = mapping.copy({'index_reference': index})['index_reference']
        child_ref = archive.read('b', copied)['content'].split(': ')[1]
        self.assertEqual(archive.read('b', child_ref)['content'], 'full evidence')
        with self.assertRaises(ValueError):
            archive.read('b', future)

    def test_foreign_partial_turn_later_completes_without_changing_old_node(self):
        store = self.store()
        self.sessions.append_event('cli', 'user', {'text': 'question'})
        self.sessions.append_event('cli', 'token', {'text': 'partial'})
        store.import_sessions()
        branch = store.branches()[0]
        original = store.path(branch['id'])[0]
        self.sessions.append_event('cli', 'final', {'text': 'complete'})
        store.import_sessions()
        path = store.path(branch['id'])
        self.assertEqual(len(path), 1)
        self.assertEqual((path[0]['answer'], path[0]['status']), ('complete', 'completed'))
        self.assertNotEqual(path[0]['id'], original['id'])
        self.assertEqual(store.node_events(store.node(original['id']))[-1]['data']['text'], 'partial')


if __name__ == '__main__':
    unittest.main()
