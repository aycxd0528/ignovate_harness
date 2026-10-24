import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from nailong.core.sessions import ProjectSessionStore
from nailong.gui.controller import GUIController


class EmptyConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / 'project'
        self.project.mkdir()
        self.sessions = ProjectSessionStore(self.project, base_dir=Path(self.temp.name) / 'data')
        self.controller = GUIController(self.project, sessions=self.sessions)

    async def asyncTearDown(self):
        await self.controller.close()
        self.temp.cleanup()

    async def test_cleanup_removes_only_unused_conversations_and_persists(self):
        empty = await self.controller.command('create', {})
        active = await self.controller.command('create', {})
        draft = await self.controller.command('create', {})
        await self.controller.command('draft', {'branch_id': draft['id'], 'text': '还没发送的问题'})
        result = await self.controller.command('prune_empty', {'keep_branch_ids': [active['id']]})
        self.assertEqual(result['removed'], [empty['id']])
        self.assertEqual({b['id'] for b in self.controller.snapshot()['branches']}, {active['id'], draft['id']})
        self.assertEqual(self.controller.branches.draft(draft['id']), '还没发送的问题')
        self.assertNotIn(empty['id'], self.controller.branch_modes)
        self.assertIsNone(self.controller.branches.db.execute('SELECT 1 FROM gui_branch_modes WHERE branch_id=?', (empty['id'],)).fetchone())
        self.controller.branches.import_sessions()
        self.assertNotIn(empty['id'], {b['id'] for b in self.controller.branches.branches()})

    async def test_history_forks_and_attempted_requests_are_kept(self):
        history = self.controller.branches.create()
        self.sessions.append_event(history['thread_id'], 'user', {'text': '已有问题'})
        self.sessions.append_event(history['thread_id'], 'final', {'text': '已有回答'})
        node = self.controller.branches.commit_turn(history['id'], 0, 'cp-root')
        fork = self.controller.branches.create_fork(history['id'], node['id'], '追问', 'cp')
        attempted = self.controller.branches.create()
        payload = {'message': '尚未进入模型就失败的问题'}
        with self.controller.branches.db:
            self.controller.branches.db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)', ('failed-run', attempted['id'], 'failed', json.dumps(payload), None, None))
        raw = self.controller.branches.create()
        self.sessions.session_path(raw['thread_id']).write_text('unreadable history\n')
        self.assertEqual((await self.controller.command('prune_empty', {}))['removed'], [])
        self.assertEqual({b['id'] for b in self.controller.branches.branches()}, {history['id'], fork['id'], attempted['id'], raw['id']})

    async def test_reuse_is_limited_to_the_current_truly_empty_conversation(self):
        blank = await self.controller.command('create', {})
        reused = await self.controller.command('create', {'reuse_branch_id': blank['id']})
        self.assertEqual(reused['id'], blank['id'])
        await self.controller.command('draft', {'branch_id': blank['id'], 'text': '保存我的草稿'})
        fresh = await self.controller.command('create', {'reuse_branch_id': blank['id']})
        self.assertNotEqual(fresh['id'], blank['id'])
        self.assertEqual(self.controller.branches.draft(blank['id']), '保存我的草稿')

    async def test_browser_protected_map_draft_survives_cleanup(self):
        map_draft = await self.controller.command('create', {})
        blank = await self.controller.command('create', {})
        result = await self.controller.command('prune_empty', {'keep_branch_ids': [map_draft['id']]})
        self.assertEqual(result['removed'], [blank['id']])
        self.assertEqual(self.controller.branches.branch(map_draft['id'])['id'], map_draft['id'])

    async def test_checkpoint_without_visible_messages_is_kept(self):
        branch = await self.controller.command('create', {})
        with closing(sqlite3.connect(self.sessions.database_path)) as db:
            db.execute('CREATE TABLE checkpoints (thread_id TEXT)')
            db.execute('INSERT INTO checkpoints VALUES (?)', (branch['thread_id'],))
            db.commit()
        self.assertEqual((await self.controller.command('prune_empty', {}))['removed'], [])

    async def test_checkpoint_inspection_releases_its_database_handles(self):
        from unittest.mock import patch
        from nailong.gui.empty_conversations import empty_branch_ids
        with closing(sqlite3.connect(self.sessions.database_path)) as db:
            db.execute('CREATE TABLE checkpoints (thread_id TEXT)')
        connections = []
        connect = sqlite3.connect

        def capture_connection(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connections.append(connection)
            return connection

        try:
            with patch('nailong.gui.empty_conversations.sqlite3.connect', side_effect=capture_connection):
                empty_branch_ids(self.controller)
            self.assertTrue(connections)
            for connection in connections:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute('SELECT 1')
        finally:
            for connection in connections:
                connection.close()

    async def test_task_without_visible_messages_is_kept(self):
        from nailong.core.task_state import TaskStore
        branch = await self.controller.command('create', {})
        TaskStore(self.sessions).begin(branch['thread_id'], '等待后续处理的任务')
        self.assertEqual((await self.controller.command('prune_empty', {}))['removed'], [])

    async def test_empty_external_log_does_not_reappear_as_history(self):
        self.sessions.session_path('empty-external').touch()
        self.controller.branches.import_sessions()
        self.assertEqual(self.controller.branches.branches(), [])

    async def test_damaged_nonempty_external_history_is_still_visible(self):
        self.sessions.session_path('damaged-external').write_text('unreadable historical content\n')
        self.controller.branches.import_sessions()
        self.assertEqual([b['thread_id'] for b in self.controller.branches.branches()], ['damaged-external'])
        self.assertEqual((await self.controller.command('prune_empty', {}))['removed'], [])

    async def test_unreadable_task_authority_prevents_cleanup_without_breaking_history(self):
        from nailong.core.task_state import TaskStore
        branch = await self.controller.command('create', {})
        tasks = TaskStore(self.sessions)
        tasks.begin(branch['thread_id'], '保留任务')
        authority = next(tasks.directory.glob('*.json'))
        authority.write_text('{broken json')
        self.assertEqual((await self.controller.command('prune_empty', {}))['removed'], [])
        self.assertEqual(self.controller.snapshot()['branches'][0]['id'], branch['id'])

    async def test_busy_project_requests_cleanup_retry_without_deleting_content(self):
        from nailong.core.file_locks import file_lock
        branch = await self.controller.command('create', {})
        with file_lock(self.sessions.root / 'project-execution.lock'):
            result = await self.controller.command('prune_empty', {})
        self.assertEqual(result['removed'], [])
        self.assertTrue(result.get('retry'))
        self.assertEqual((await self.controller.command('prune_empty', {}))['removed'], [branch['id']])

    def _checkpoint_database(self):
        from contextlib import closing
        with closing(sqlite3.connect(self.sessions.database_path)) as db, db:
            db.execute('CREATE TABLE checkpoints (thread_id TEXT)')

    def _assert_native_closed(self,connections):
        self.assertTrue(connections)
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):connection.execute('SELECT 1')

    async def test_repeated_checkpoint_checks_close_actual_read_connections_before_return(self):
        import os
        from unittest.mock import patch
        from nailong.gui.empty_conversations import empty_branch_ids
        self._checkpoint_database()
        branch=self.controller.branches.create()
        native=sqlite3.connect;connections=[]
        def connect(*args,**kwargs):
            connection=native(*args,**kwargs);connections.append(connection);return connection
        before=len(os.listdir('/dev/fd')) if Path('/dev/fd').exists() else None
        try:
            with patch('nailong.gui.empty_conversations.sqlite3.connect',side_effect=connect):
                for _ in range(12):self.assertEqual(empty_branch_ids(self.controller),[branch['id']])
            self.assertEqual(len(connections),12)
            self._assert_native_closed(connections)
            if before is not None:self.assertEqual(len(os.listdir('/dev/fd')),before)
        finally:
            for connection in connections:connection.close()

    async def test_failed_checkpoint_query_closes_native_connection_and_preserves_branch(self):
        from unittest.mock import patch
        from nailong.gui.empty_conversations import empty_branch_ids
        self._checkpoint_database();branch=self.controller.branches.create()
        native=sqlite3.connect;connections=[]
        class BrokenRead(sqlite3.Connection):
            def execute(self,query,*args,**kwargs):
                if 'sqlite_master' in query:raise sqlite3.OperationalError('native checkpoint query failed')
                return super().execute(query,*args,**kwargs)
        def connect(*args,**kwargs):
            connection=native(*args,**kwargs,factory=BrokenRead);connections.append(connection);return connection
        try:
            with patch('nailong.gui.empty_conversations.sqlite3.connect',side_effect=connect):
                self.assertEqual(empty_branch_ids(self.controller),[])
            self._assert_native_closed(connections)
            self.assertEqual(self.controller.branches.branch(branch['id'])['id'],branch['id'])
        finally:
            for connection in connections:connection.close()

    async def test_cancelled_checkpoint_query_closes_native_connection_and_propagates(self):
        import asyncio
        from unittest.mock import patch
        from nailong.gui.empty_conversations import empty_branch_ids
        self._checkpoint_database();branch=self.controller.branches.create()
        native=sqlite3.connect;connections=[];cancelled=asyncio.CancelledError('checkpoint read cancelled')
        class CancelledRead(sqlite3.Connection):
            def execute(self,query,*args,**kwargs):
                if 'sqlite_master' in query:raise cancelled
                return super().execute(query,*args,**kwargs)
        def connect(*args,**kwargs):
            connection=native(*args,**kwargs,factory=CancelledRead);connections.append(connection);return connection
        try:
            with patch('nailong.gui.empty_conversations.sqlite3.connect',side_effect=connect):
                with self.assertRaises(asyncio.CancelledError) as caught:empty_branch_ids(self.controller)
            self.assertIs(caught.exception,cancelled)
            self._assert_native_closed(connections)
            self.assertEqual(self.controller.branches.branch(branch['id'])['id'],branch['id'])
        finally:
            for connection in connections:connection.close()

    async def test_missing_checkpoint_database_is_not_created_by_empty_check(self):
        from unittest.mock import patch
        from nailong.gui.empty_conversations import empty_branch_ids
        branch=self.controller.branches.create()
        self.assertFalse(self.sessions.database_path.exists())
        with patch('nailong.gui.empty_conversations.sqlite3.connect',side_effect=AssertionError('must not open absent database')):
            self.assertEqual(empty_branch_ids(self.controller),[branch['id']])
        self.assertFalse(self.sessions.database_path.exists())

    async def test_dangling_checkpoint_link_preserves_conversation_as_unknown(self):
        branch=self.controller.branches.create()
        try:self.sessions.database_path.symlink_to('missing-checkpoints.sqlite')
        except (OSError,NotImplementedError) as error:self.skipTest(f'native symlink unavailable: {error}')
        self.assertEqual((await self.controller.command('prune_empty',{}))['removed'],[])
        self.assertEqual(self.controller.branches.branch(branch['id'])['id'],branch['id'])
        self.assertEqual(self.sessions.database_path.readlink(),Path('missing-checkpoints.sqlite'))

    async def test_cyclic_checkpoint_link_preserves_conversation_as_unknown(self):
        branch=self.controller.branches.create()
        try:self.sessions.database_path.symlink_to(self.sessions.database_path.name)
        except (OSError,NotImplementedError) as error:self.skipTest(f'native symlink unavailable: {error}')
        self.assertEqual((await self.controller.command('prune_empty',{}))['removed'],[])
        self.assertEqual(self.controller.branches.branch(branch['id'])['id'],branch['id'])
        self.assertEqual(self.sessions.database_path.readlink(),Path(self.sessions.database_path.name))

    async def test_readable_checkpoint_link_keeps_its_committed_evidence(self):
        branch=self.controller.branches.create();target=self.sessions.root/'real-checkpoints.sqlite'
        with closing(sqlite3.connect(target)) as db, db:
            db.execute('CREATE TABLE checkpoints(thread_id TEXT)')
            db.execute('INSERT INTO checkpoints VALUES(?)',(branch['thread_id'],))
        before=target.read_bytes()
        try:self.sessions.database_path.symlink_to(target.name)
        except (OSError,NotImplementedError) as error:self.skipTest(f'native symlink unavailable: {error}')
        self.assertEqual((await self.controller.command('prune_empty',{}))['removed'],[])
        self.assertEqual(self.controller.branches.branch(branch['id'])['id'],branch['id'])
        self.assertEqual(target.read_bytes(),before)
        self.assertEqual(self.sessions.database_path.readlink(),Path(target.name))
