from platform_fixtures import assert_private
import hashlib
import importlib
import tempfile
import unittest
from pathlib import Path

from nailong.core.skills import SkillRegistry


def skill(root,name,description='first'):
    path=root/name/'SKILL.md'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(f'---\nname: {name}\ndescription: {description}\n---\nbody {description}\n', newline='\n')
    return path


class WorkflowSkillsMemoryTests(unittest.TestCase):
    def test_hot_reload_adds_changes_removes_and_reports_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            registry=SkillRegistry(root,user_root=root/'users')
            reload=getattr(registry,'reload',None)
            self.assertTrue(callable(reload),'Skill hot reload is not implemented')
            path=skill(root/'.agents/skills','probe')
            (root/'.agents/skills/SUPERPOWERS-LICENSE').write_text('license notice', newline='\n')
            bad=root/'.agents/skills/bad/SKILL.md'
            bad.parent.mkdir(parents=True)
            bad.write_text('no frontmatter', newline='\n')
            result=reload()
            self.assertIn('probe',result['added'])
            self.assertTrue(registry.diagnostics)
            self.assertIn('frontmatter',str(registry.diagnostics))
            self.assertNotIn('SUPERPOWERS-LICENSE',str(registry.diagnostics))
            path.write_text(path.read_text().replace('first','second'), newline='\n')
            self.assertIn('probe',reload()['changed'])
            self.assertIn('second',registry.catalog())
            path.unlink()
            self.assertIn('probe',reload()['removed'])

    def test_disabled_project_skill_does_not_fall_back_and_is_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            skill(root/'.agents/skills','probe','project')
            skill(root/'users','probe','user')
            registry=SkillRegistry(root,user_root=root/'users')
            toggle=getattr(registry,'set_enabled',None)
            self.assertTrue(callable(toggle),'Skill enable/disable is not implemented')
            toggle('probe',False)
            self.assertNotIn('probe',registry.catalog())
            self.assertFalse(registry.load_skill('probe')['ok'])
            restored=SkillRegistry(root,user_root=root/'users')
            self.assertFalse(restored.load_skill('probe')['ok'])
            restored.set_enabled('probe',True)
            self.assertEqual(restored.load_skill('probe')['source'],'项目')

    def test_staged_memory_is_not_written_and_concurrent_edit_is_preserved(self):
        try: cls=importlib.import_module('nailong.core.memory').MemoryStore
        except AttributeError: self.fail('MemoryStore is not implemented')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=root/'.nailong/context.md'
            path.parent.mkdir()
            path.write_text('original', newline='\n')
            store=cls(root,user_file=root/'user/context.md')
            staged=store.stage('project','updated')
            self.assertEqual(path.read_text(),'original')
            path.write_text('concurrent', newline='\n')
            with self.assertRaisesRegex(ValueError,'冲突'): store.commit(staged)
            self.assertEqual(path.read_text(),'concurrent')
            store.commit(store.stage('project','updated'))
            self.assertEqual(path.read_text(),'updated')
            assert_private(self, path)

    def test_memory_scope_and_symlink_cannot_read_unrelated_files(self):
        try: cls=importlib.import_module('nailong.core.memory').MemoryStore
        except AttributeError: self.fail('MemoryStore is not implemented')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            store=cls(root,user_file=root/'user/context.md')
            with self.assertRaises(ValueError): store.stage('../secret','text')
            target=root/'.nailong/context.md'
            target.parent.mkdir()
            outside=root/'unrelated'
            outside.write_text('unchanged', newline='\n')
            target.symlink_to(outside)
            with self.assertRaises(ValueError): store.stage('project','text')
