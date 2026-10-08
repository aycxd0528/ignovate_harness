from platform_fixtures import assert_private
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ui.theme import load_theme


def preferences(test, root):
    try:
        cls = importlib.import_module('nailong.core.preferences').PreferenceStore
    except ModuleNotFoundError:
        test.fail('PreferenceStore is not implemented')
    return cls(root, user_path=root/'user/preferences.json')


class WorkflowPreferenceTests(unittest.TestCase):
    def test_precedence_and_persistent_config_preserve_other_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = preferences(self, root)
            store.user_path.parent.mkdir()
            store.user_path.write_text(json.dumps({'theme': 'light'}), newline='\n')
            config = root/'.nailong/settings.json'
            config.parent.mkdir()
            config.write_text(json.dumps({'theme': 'dark', 'models': {'fast': {'model':'private-fast'}}, 'model':'fast'}), newline='\n')
            local = root/'.nailong/settings.local.json'
            local.write_text(json.dumps({'theme':'light', 'hook_approvals':['keep']}), newline='\n')
            values = store.effective('original', cli={'theme':'ansi'})
            self.assertEqual(values['theme'], 'ansi')
            self.assertEqual(values['model'], 'private-fast')
            store.set('theme','dark')
            saved = json.loads(local.read_text())
            self.assertEqual(saved['hook_approvals'], ['keep'])
            self.assertEqual(saved['theme'], 'dark')
            assert_private(self, local)
            self.assertEqual(store.effective('original', cli={'theme':'ansi'})['theme'], 'dark')
            self.assertEqual(preferences(self,root).effective('original',cli={'theme':'ansi'})['theme'],'ansi')

    def test_bad_config_and_symlink_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = preferences(self,root)
            local = root/'.nailong/settings.local.json'
            local.parent.mkdir()
            local.write_text('{broken', newline='\n')
            with self.assertRaises(ValueError): store.set('theme','light')
            self.assertEqual(local.read_text(),'{broken')
            local.unlink()
            target = root/'other.json'
            target.write_text('{}', newline='\n')
            local.symlink_to(target)
            with self.assertRaises(ValueError): store.set('theme','light')
            self.assertEqual(target.read_text(),'{}')

    def test_model_configuration_cannot_override_api_host_or_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = preferences(self,root)
            config=root/'.nailong/settings.json'
            config.parent.mkdir()
            config.write_text(json.dumps({'models':{'bad':{'model':'other','api_base':'https://evil.invalid'}}}), newline='\n')
            with self.assertRaises(ValueError): store.effective('original')
            with self.assertRaises(ValueError): store.set('api_key','never')

    def test_light_and_ansi_themes_keep_no_color_and_ascii_fallback(self):
        try:
            with patch.dict(os.environ,{},clear=True):
                dark,light = load_theme(name='dark'),load_theme(name='light')
        except TypeError:
            self.fail('Named terminal themes are not implemented')
        self.assertNotEqual(dark.body_user,light.body_user)
        with patch.dict(os.environ, {'NO_COLOR':'1'}):
            ansi=load_theme(name='ansi',ascii_only=True)
            self.assertTrue(ansi.no_color)
            self.assertIsNone(ansi.role_user)
            self.assertEqual(ansi.gutter,'|')

class CliPreferenceTests(unittest.TestCase):
    def test_factory_and_session_changes_override_startup_cli_in_order(self):
        from config import Settings
        from agent import AgentRuntimeFactory
        from nailong.core.sessions import ProjectSessionStore
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as project,tempfile.TemporaryDirectory() as data:
            root=Path(project).resolve()
            try: settings=Settings('key','https://api.invalid','deepseek-flash',root,{'theme':'ansi','output_style':'detailed'})
            except TypeError: self.fail('CLI preference overrides missing')
            factory=AgentRuntimeFactory(settings,session_store=ProjectSessionStore(root,base_dir=data))
            self.addCleanup(factory.close)
            self.assertEqual(factory.output_style,'detailed')
            self.assertEqual(factory.preferences.effective()['theme'],'ansi')
            factory.preferences.set('theme','light')
            self.assertEqual(factory.preferences.effective()['theme'],'light')
