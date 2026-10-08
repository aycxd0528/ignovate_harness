"""Explicit full access stays local to one execution; no provider calls."""

import asyncio
import json
import os
import shutil
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import local_tools
from nailong.core.permissions import PermissionEngine
from nailong.core.progress import assess_progress
from nailong.tools.execution import ToolExecutionContext
from nailong.tools.files import FileSession
from tools import build_tools


class UnrestrictedFilesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'project'
        self.external = self.base / 'outside'
        self.root.mkdir()
        self.external.mkdir()
        for folder in (self.root, self.external):
            (folder / 'sample.txt').write_text('needle before\nsecond\n', newline='\n')
            (folder / '.env').write_text('needle synthetic-token\n', newline='\n')
            (folder / '.git').mkdir()
            (folder / '.git' / 'config').write_text('needle configuration\n', newline='\n')
        self.session = FileSession(self.root)
        self.execution = self.context('bypassPermissions')
        self.normal = self.context('default')
        self.tools = self.bundle(self.execution)
        self.normal_tools = self.bundle(self.normal, FileSession(self.root))

    def context(self, mode, **kwargs):
        return ToolExecutionContext(self.root, permission_mode=mode,
            permission_engine=PermissionEngine(self.root, rules={}),
            artifact_directory=self.base / ('artifacts-' + mode), **kwargs)

    def bundle(self, execution, session=None, **kwargs):
        return {tool.name: tool for tool in build_tools(file_session=session or self.session,
            execution_context=execution, **kwargs)}

    def call(self, name, args, tools=None):
        return json.loads((tools or self.tools)[name].invoke(args))

    def test_external_absolute_and_relative_reads_keep_unambiguous_paths(self):
        for path in (str(self.external / 'sample.txt'), '../outside/sample.txt'):
            with self.subTest(path=path):
                result = self.call('read_file', {'path': path})
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['path'], str(self.external / 'sample.txt'))
                self.assertTrue(result['read_complete'])
                self.assertFalse(self.call('read_file', {'path': path}, self.normal_tools)['ok'])
        self.assertEqual(self.call('read_file', {'path': 'sample.txt'})['path'], 'sample.txt')

    def test_external_create_edit_and_replace_share_the_read_version_contract(self):
        target = self.external / 'new.txt'
        result = self.call('write_file', {'path': str(target), 'content': 'before'})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['path'], str(target))
        self.assertFalse(self.call('edit_file', {'path': str(target),
            'old_string': 'before', 'new_string': 'after'})['ok'])
        self.call('read_file', {'path': str(target)})
        result = self.call('edit_file', {'path': '../outside/new.txt',
            'old_string': 'before', 'new_string': 'after'})
        self.assertTrue(result['ok'], result)
        self.assertEqual(target.read_text(), 'after')
        self.call('read_file', {'path': str(target)})
        self.assertTrue(self.call('write_file', {'path': str(target), 'content': 'replacement'})['ok'])

    def test_external_stale_and_partial_read_cannot_overwrite(self):
        target = self.external / 'sample.txt'
        self.call('read_file', {'path': str(target), 'max_chars': 3})
        self.assertFalse(self.call('write_file', {'path': str(target), 'content': 'lost'})['ok'])
        self.call('read_file', {'path': str(target)})
        target.write_text('external change', newline='\n')
        self.assertFalse(self.call('edit_file', {'path': str(target),
            'old_string': 'external change', 'new_string': 'lost'})['ok'])
        self.assertEqual(target.read_text(), 'external change')

    def test_protected_files_and_directories_are_visible_only_in_explicit_mode(self):
        for path in ('.env', '.git/config', str(self.external / '.env')):
            with self.subTest(path=path):
                self.assertTrue(self.call('read_file', {'path': path})['ok'])
                self.assertFalse(self.call('read_file', {'path': path}, self.normal_tools)['ok'])
        files = self.call('list_files', {})['files']
        self.assertIn('.env', files)
        self.assertIn('.git/config', files)
        self.assertNotIn('.env', self.call('list_files', {}, self.normal_tools)['files'])

    def test_protected_file_edit_and_creation_keep_the_normal_boundary(self):
        self.call('read_file', {'path': '.env'})
        result = self.call('edit_file', {'path': '.env', 'old_string': 'synthetic-token',
            'new_string': 'synthetic-updated'})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['path'], '.env')
        result = self.call('write_file', {'path': '.git/new.txt', 'content': 'synthetic configuration'})
        self.assertTrue(result['ok'], result)
        self.assertFalse(self.call('write_file', {'path': '.git/default-denied.txt',
            'content': 'blocked'}, self.normal_tools)['ok'])
        self.assertFalse((self.root / '.git/default-denied.txt').exists())

    def test_external_list_glob_and_literal_search_report_absolute_paths(self):
        expected = {str(self.external / name) for name in ('sample.txt', '.env', '.git/config')}
        listed = self.call('list_files', {'path': '../outside'})
        self.assertTrue(listed['ok'], listed)
        self.assertEqual(set(listed['files']), expected)
        globbed = self.call('glob', {'path': str(self.external), 'pattern': '**/*'})
        self.assertEqual(set(globbed['files']), expected)
        matches = self.call('grep', {'path': str(self.external), 'pattern': 'needle',
            'pattern_mode': 'literal', 'output_mode': 'count'})
        self.assertEqual(set(matches['counts']), expected)
        self.assertTrue(matches['coverage_complete'])
        searched = self.call('search_text', {'path': str(self.external), 'query': 'needle'})
        self.assertEqual({item['path'] for item in searched['matches']}, expected)

    @unittest.skipUnless(shutil.which('rg'), 'ripgrep is optional')
    def test_external_regex_search_includes_protected_files(self):
        result = self.call('grep', {'path': str(self.external), 'pattern': '^needle',
            'output_mode': 'files_with_matches'})
        self.assertTrue(result['ok'], result)
        self.assertEqual(set(result['files']), {str(self.external / name)
            for name in ('sample.txt', '.env', '.git/config')})

    @unittest.skipIf(os.name == 'nt', 'Native Windows rejects all reparse paths even in full access; native junction tests cover that contract.')
    def test_symlink_file_and_directory_escapes_are_scoped_and_cycles_terminate(self):
        (self.root / 'linked.txt').symlink_to(self.external / 'sample.txt')
        (self.root / 'linked-directory').symlink_to(self.external, target_is_directory=True)
        (self.external / 'cycle').symlink_to(self.root, target_is_directory=True)
        self.assertTrue(self.call('read_file', {'path': 'linked.txt'})['ok'])
        self.assertFalse(self.call('read_file', {'path': 'linked.txt'}, self.normal_tools)['ok'])
        result = self.call('glob', {'pattern': '**/*.txt'})
        self.assertTrue(result['ok'], result)
        self.assertIn(str(self.external / 'sample.txt'), result['files'])
        self.assertEqual(len(result['files']), len(set(result['files'])))
        result = self.call('grep', {'pattern': 'needle', 'include': '*.txt', 'pattern_mode': 'literal'})
        self.assertTrue(result['ok'], result)
        self.assertIn(str(self.external / 'sample.txt'), {item['path'] for item in result['matches']})

    def test_preview_scope_restores_default_and_cannot_grant_other_project_access(self):
        with self.assertRaises(ValueError):
            self.session.resolve(str(self.external))
        with self.execution.file_access_scope():
            self.assertEqual(self.session.resolve('../outside'), self.external)
            self.assertEqual(local_tools.resolve_project_path('../outside'), self.external)
            self.assertTrue(local_tools.read_file('.env')['ok'])
            self.assertIn('.git/config', local_tools.list_files()['files'])
            with self.normal.file_access_scope():
                with self.assertRaises(ValueError):
                    self.session.resolve('../outside')
            with self.assertRaises(ValueError):
                FileSession(self.external).resolve(str(self.root))
        with self.assertRaises(ValueError):
            self.session.resolve('../outside')

    def test_external_review_target_builds_but_remains_read_only_and_target_scoped(self):
        reviewed = self.bundle(self.execution, profile='review', target_path=str(self.external))
        result = self.call('read_file', {'path': str(self.external / 'sample.txt')}, reviewed)
        self.assertTrue(result['ok'], result)
        self.assertFalse(self.call('read_file', {'path': str(self.root / 'sample.txt')}, reviewed)['ok'])
        self.assertNotIn('write_file', reviewed)

    def test_external_review_selected_paths_use_absolute_paths_and_preserve_selection(self):
        selected = {str(self.external / 'sample.txt'), str(self.external / '.env')}
        reviewed = self.bundle(self.execution, profile='review', target_path=str(self.external),
            review_paths=frozenset(selected))
        listed = self.call('list_files', {}, reviewed)
        self.assertTrue(listed['ok'], listed)
        self.assertEqual(set(listed['files']), selected)
        self.assertTrue(self.call('read_file', {'path': str(self.external / '.env')}, reviewed)['ok'])
        self.assertFalse(self.call('read_file', {'path': str(self.external / '.git/config')}, reviewed)['ok'])
        with self.assertRaises(ValueError):
            self.bundle(self.normal, profile='review', target_path=str(self.external))

    def test_bypass_preserves_profile_metadata_restrictions_and_schema_validation(self):
        tool = self.tools['write_file']
        for profile in ('plan', 'review', 'subagent', 'init'):
            with self.subTest(profile=profile):
                spec = replace(tool.spec, profiles=frozenset({profile}))
                result = self.execution.invoke_sync(spec,
                    {'path': str(self.external / 'forbidden.txt'), 'content': 'blocked'},
                    profile=profile, file_session=self.session)
                self.assertFalse(result['ok'])
                self.assertFalse(result['started'])
        result = self.call('read_file', {'path': str(self.external / 'sample.txt'), 'offset': '1'})
        self.assertEqual(result['error_code'], 'invalid_parameters')
        self.assertFalse(result['started'])

    def test_read_only_plan_can_read_external_files(self):
        planned = self.bundle(self.execution, profile='plan')
        result = self.call('read_file', {'path': str(self.external / 'sample.txt')}, planned)
        self.assertTrue(result['ok'], result)

    def test_real_configured_key_is_redacted_in_protected_file_result(self):
        secret = 'synthetic-actual-configured-key'
        (self.root / '.env').write_text('API_KEY=' + secret, newline='\n')
        execution = self.context('bypassPermissions', api_key=secret)
        tools = self.bundle(execution)
        result = self.call('read_file', {'path': '.env'}, tools)
        self.assertTrue(result['ok'], result)
        self.assertNotIn(secret, json.dumps(result))

    def test_parallel_threads_keep_default_and_unrestricted_scopes_separate(self):
        barrier = threading.Barrier(2)
        def read(tools):
            def synchronized(**kwargs):
                barrier.wait(timeout=5)
                return self.session.read_file(str(self.external / 'sample.txt'))
            tools['read_file'].spec = replace(tools['read_file'].spec, handler=synchronized)
            # Both gates accept the project path; each handler still has its own scope.
            return self.call('read_file', {'path': 'sample.txt'}, tools)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(read, (self.tools, self.normal_tools)))
        self.assertTrue(results[0]['ok'])
        self.assertFalse(results[1]['ok'])
        self.assertFalse(self.session.read_file(str(self.external / 'sample.txt'))['ok'])

    def test_parallel_async_scopes_and_detached_task_cannot_keep_an_expired_grant(self):
        async def run():
            ready = asyncio.Event()
            count = 0
            async def handler(**kwargs):
                nonlocal count
                count += 1
                if count == 2:
                    ready.set()
                await ready.wait()
                return self.session.read_file(str(self.external / 'sample.txt'))
            for tools in (self.tools, self.normal_tools):
                tool = tools['read_file']
                tool.spec = replace(tool.spec, async_handler=handler)
            results = await asyncio.gather(*(tools['read_file'].ainvoke({'path': 'sample.txt'})
                for tools in (self.tools, self.normal_tools)))
            self.assertTrue(json.loads(results[0])['ok'])
            self.assertFalse(json.loads(results[1])['ok'])
            finished = asyncio.Event()
            async def detached():
                await finished.wait()
                return self.session.read_file(str(self.external / 'sample.txt'))
            with self.execution.file_access_scope():
                task = asyncio.create_task(detached())
            finished.set()
            self.assertFalse((await task)['ok'])
        asyncio.run(run())

    def test_schema_failure_observed_before_sync_tool_returns_and_breaks_progress(self):
        observations = []
        history = []
        decisions = []
        def observe(row):
            nonlocal history
            observations.append(row)
            decision = assess_progress(history, {'tool': row['name'], **row})
            decisions.append(decision['action'])
            history = decision['observations']
        execution = self.context('bypassPermissions', observer=observe)
        tools = self.bundle(execution)
        for index in range(3):
            self.call('read_file', {'path': 'sample.txt'}, tools)
        message = tools['read_file'].invoke({'type': 'tool_call', 'name': 'read_file',
            'args': {'path': 'sample.txt', 'offset': 'invalid'}, 'id': 'bad-schema'},
            config={'configurable': {'thread_id': 'schema-owner'}})
        self.assertEqual(message.status, 'error')
        self.assertEqual(len(observations), 4)
        self.assertEqual(observations[-1]['call_id'], 'bad-schema')
        self.assertEqual(observations[-1]['thread_id'], 'schema-owner')
        self.assertEqual(observations[-1]['status'], 'failed')
        self.assertFalse(observations[-1]['ok'])
        self.assertFalse(observations[-1]['started'])
        for index in range(3):
            self.call('read_file', {'path': 'sample.txt'}, tools)
        self.assertNotIn('pause', decisions)
        self.assertEqual(len(observations), 7)

    def test_schema_failure_async_observer_is_awaited_once_before_return(self):
        async def run():
            observations = []
            async def observe(row):
                await asyncio.sleep(0)
                observations.append(row)
            execution = self.context('bypassPermissions', observer=observe)
            tools = self.bundle(execution)
            result = await tools['read_file'].ainvoke({'path': str(self.external), 'unexpected': True})
            self.assertEqual(json.loads(result)['error_code'], 'invalid_parameters')
            self.assertEqual(len(observations), 1)
            self.assertFalse(observations[0]['started'])
        asyncio.run(run())

    def test_schema_failure_with_non_json_python_arguments_still_reports_error(self):
        observations = []
        execution = self.context('bypassPermissions', observer=observations.append)
        tools = self.bundle(execution)
        message = tools['read_file'].invoke({'type': 'tool_call', 'name': 'read_file',
            'args': {'path': b'invalid-schema'}, 'id': 'native-python-input'})
        self.assertEqual(message.status, 'error')
        self.assertEqual(json.loads(message.content)['error_code'], 'invalid_parameters')
        self.assertEqual(len(observations), 1)
        self.assertFalse(observations[0]['started'])

    def test_external_json_clipping_clears_full_read_eligibility_within_scope(self):
        target = self.external / 'encoded.txt'
        target.write_text('"' * 10000, newline='\n')
        result = self.call('read_file', {'path': str(target)})
        self.assertTrue(result['ok'])
        self.assertTrue(result['result_truncated'])
        self.assertFalse(result['read_complete'])
        result = self.call('write_file', {'path': str(target), 'content': 'replacement'})
        self.assertFalse(result['ok'])
        self.assertEqual(target.read_text(), '"' * 10000)

    def test_async_calls_and_cancellation_restore_scope(self):
        async def run():
            unrestricted = await self.tools['read_file'].ainvoke({'path': str(self.external / 'sample.txt')})
            normal = await self.normal_tools['read_file'].ainvoke({'path': str(self.external / 'sample.txt')})
            self.assertTrue(json.loads(unrestricted)['ok'])
            self.assertFalse(json.loads(normal)['ok'])
            entered = asyncio.Event()
            async def blocked(**kwargs):
                self.assertEqual(self.session.resolve(str(self.external)), self.external)
                entered.set()
                await asyncio.Event().wait()
            tool = self.tools['read_file']
            tool.spec = replace(tool.spec, async_handler=blocked)
            task = asyncio.create_task(tool.ainvoke({'path': str(self.external / 'sample.txt')}))
            await asyncio.wait_for(entered.wait(), timeout=5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(self.session.read_file(str(self.external / 'sample.txt'))['ok'])
        asyncio.run(run())
