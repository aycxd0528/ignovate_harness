import importlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from nailong.tools.files import FileSession
from nailong.tools.registry import build_tool_specs


class WorkflowGitTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name).resolve()
        self.git('init','-q')
        self.git('config','user.email','fixture@example.invalid')
        self.git('config','user.name','Fixture')

    def git(self,*args):
        return subprocess.run(['git',*args],cwd=self.root,check=True,capture_output=True).stdout.decode()

    def service(self,root=None):
        try: cls=importlib.import_module('nailong.core.git_changes').GitChanges
        except ModuleNotFoundError: self.fail('GitChanges is not implemented')
        return cls(root or self.root)

    def commit(self):
        self.git('add','-A')
        self.git('commit','-qm','fixture')

    def test_magic_path_is_literal_and_cannot_expand_protected_files(self):
        (self.root/'.venv').mkdir()
        (self.root/'.venv/private.txt').write_text('before\n')
        (self.root/':(glob)**').write_text('before\n')
        self.commit()
        (self.root/'.venv/private.txt').write_text('PROTECTED_TEST_SENTINEL\n')
        (self.root/':(glob)**').write_text('safe change\n')
        changes=self.service().select()
        self.assertIn('safe change',changes.patch)
        self.assertNotIn('PROTECTED_TEST_SENTINEL',changes.patch)
        self.assertTrue(any(row['path']=='.venv/private.txt' for row in changes.skipped))

    def test_working_staged_and_untracked_are_distinct(self):
        file=self.root/'file with space.py'
        file.write_text('before\n')
        self.commit()
        file.write_text('staged\n')
        self.git('add','--',file.name)
        file.write_text('working\n')
        (self.root/'new.py').write_text('new\n')
        service=self.service()
        working=service.select()
        self.assertIn('working',working.patch)
        self.assertIn('new.py',working.paths)
        staged=service.select('staged')
        self.assertIn('staged',staged.patch)
        self.assertNotIn('+working',staged.patch)
        self.assertNotIn('new.py',staged.paths)

    def test_unborn_working_diff_uses_current_file_after_staging(self):
        (self.root/'a.py').write_text('staged first\n'); self.git('add','a.py')
        (self.root/'a.py').write_text('current second\n')
        self.assertIn('+current second',self.service().select().patch)
        self.assertIn('+staged first',self.service().select('staged').patch)

    def test_branch_ignores_working_tree_and_rejects_invalid_ref(self):
        file=self.root/'a.py'
        file.write_text('one\n')
        self.commit()
        base=self.git('rev-parse','HEAD').strip()
        file.write_text('two\n')
        self.commit()
        file.write_text('not committed\n')
        changes=self.service().select('branch',base)
        self.assertIn('+two',changes.patch)
        self.assertNotIn('not committed',changes.patch)
        with self.assertRaises(ValueError): self.service().select('branch','--help')

    def test_initial_and_non_git_and_empty_are_explicit(self):
        initial=self.service().select()
        self.assertFalse(initial.changes)
        (self.root/'a').write_text('new')
        self.assertIn('a',self.service().select().paths)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError): self.service(directory).select()

    def test_protected_symlink_and_binary_contents_are_excluded(self):
        (self.root/'safe.py').write_text('old\n')
        (self.root/'.env').write_text('NEVER_OLD_SECRET')
        self.commit()
        (self.root/'.env').write_text('NEVER_NEW_SECRET')
        (self.root/'binary').write_bytes(b'\x00binary secret')
        with tempfile.TemporaryDirectory() as outside:
            target=Path(outside)/'private'
            target.write_text('NEVER_OUTSIDE_SECRET')
            (self.root/'link').symlink_to(target)
            changes=self.service().select()
            self.assertNotIn('NEVER',changes.patch)
            self.assertNotIn('binary secret',changes.patch)
            self.assertTrue(changes.skipped)

    def test_deleted_renamed_and_newline_paths_are_preserved(self):
        (self.root/'old.py').write_text('many identical lines\n'*20)
        (self.root/'deleted.py').write_text('delete\n')
        self.commit()
        self.git('mv','old.py','renamed.py')
        (self.root/'deleted.py').unlink()
        unusual='-line\nbreak.py'
        (self.root/unusual).write_text('new\n')
        changes=self.service().select()
        self.assertIn('renamed.py',changes.paths)
        self.assertIn('deleted.py',changes.paths)
        self.assertIn(unusual,changes.paths)

    def test_patch_and_file_caps_report_uncovered_work(self):
        for index in range(205): (self.root/f'f{index:03}.py').write_text('text\n')
        changes=self.service().select()
        self.assertLessEqual(len(changes.changes),200)
        self.assertLessEqual(len(changes.patch.encode()),256*1024)
        self.assertTrue(changes.truncated)
        self.assertTrue(all(len(batch)<=20 for batch in changes.batches()))

    def test_external_diff_is_not_executed(self):
        file=self.root/'a.py'
        file.write_text('old\n')
        self.commit()
        self.git('config','diff.external','touch external-ran')
        file.write_text('new\n')
        self.assertIn('+new',self.service().select().patch)
        self.assertFalse((self.root/'external-ran').exists())

    def test_review_tool_scope_refuses_other_files(self):
        (self.root/'allowed.py').write_text('allowed')
        (self.root/'other.py').write_text('private')
        try:
            specs=build_tool_specs(profile='review',session=FileSession(self.root),
                                   target_path=str(self.root),review_paths=frozenset({'allowed.py'}))
        except TypeError:
            self.fail('Diff review path allowlist is not implemented')
        read=next(spec.handler for spec in specs if spec.name=='read_file')
        self.assertTrue(read('allowed.py')['ok'])
        self.assertFalse(read('other.py')['ok'])

class ReviewServiceScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_forwards_paths_without_breaking_legacy_factory(self):
        from agent_service import AgentService
        import local_tools
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            calls=[]
            def factory(**kwargs):
                calls.append(kwargs)
                return object()
            with patch.object(local_tools,'PROJECT_ROOT',Path(directory).resolve()):
                service=AgentService(factory)
                await service._runtime_async('review',directory,'thread',review_paths=frozenset({'a.py'}))
                self.assertEqual(calls[-1]['review_paths'],frozenset({'a.py'}))
                await service._runtime_async('review',directory,'thread')
                self.assertNotIn('review_paths',calls[-1])
