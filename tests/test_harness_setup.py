from platform_fixtures import assert_private
import asyncio
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class HarnessSetupTests(unittest.TestCase):
    def store(self, directory):
        try:
            module = importlib.import_module('nailong.core.bootstrap')
        except ModuleNotFoundError:
            self.fail('First-run configuration store is missing')
        return module.BootstrapStore(directory)

    def test_first_run_commit_private_config_and_no_permission_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.store(Path(directory)/'user')
            self.assertFalse(store.completed())
            store.save('https://api.deepseek.com', 'deepseek-flash', 'fixture-key', 'low')
            saved = json.loads(store.path.read_text())
            self.assertEqual(saved['provider']['api_key'], 'fixture-key')
            self.assertEqual(saved['reasoning_effort'], 'low')
            self.assertNotIn('permission_mode', saved)
            assert_private(self, store.path)
            self.assertTrue(store.completed())

    def test_blank_key_preserves_existing_key_and_failed_validation_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.store(Path(directory)/'user')
            store.save('https://api.deepseek.com', 'deepseek-flash', 'keep-key')
            store.save('https://api.deepseek.com', 'deepseek-v4-pro', '')
            self.assertEqual(store.read()['provider']['api_key'], 'keep-key')
            before = store.path.read_bytes()
            for base, model, effort in [('https://host?api_key=secret', 'deepseek-flash', 'high'),
                                       ('https://api.invalid', 'unknown', 'high')]:
                with self.assertRaises(ValueError):
                    store.save(base, model, '', effort)
                self.assertEqual(store.path.read_bytes(), before)

    def test_corrupt_or_symlink_config_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self.store(root/'user')
            store.path.parent.mkdir()
            store.path.write_text('{broken', newline='\n')
            with self.assertRaises(ValueError):
                store.save('https://api.invalid', 'deepseek-flash', 'new-key')
            self.assertEqual(store.path.read_text(), '{broken')
            store.path.unlink()
            other = root/'other.json'
            other.write_text('{}', newline='\n')
            store.path.symlink_to(other)
            with self.assertRaises(ValueError):
                store.save('https://api.invalid', 'deepseek-flash', 'new-key')
            self.assertEqual(other.read_text(), '{}')

    def test_user_connection_config_loads_without_project_env(self):
        import config
        import os
        with tempfile.TemporaryDirectory() as directory:
            store = self.store(Path(directory)/'user')
            store.save('https://api.invalid', 'deepseek-flash', 'fixture-key', 'max')
            with patch('config.load_dotenv'), patch.dict(os.environ, {}, clear=True):
                settings = config.load_settings(config_path=store.path)
            self.assertEqual(settings.api_key, 'fixture-key')
            self.assertEqual(settings.reasoning_effort, 'max')

    def test_doctor_uses_the_same_user_connection_configuration(self):
        import os
        from nailong.core.diagnostics import diagnose
        with tempfile.TemporaryDirectory() as directory:
            store = self.store(Path(directory)/'user')
            store.save('https://api.invalid', 'deepseek-flash', 'fixture-key')
            with patch('dotenv.dotenv_values', return_value={}), \
                    patch.dict(os.environ, {'IGNOVATE_CONFIG_DIR':str(store.path.parent),
                                            'USERPROFILE':directory, 'HOME':directory}, clear=True):
                report = diagnose(directory)
            credentials = next(row for row in report['checks'] if row['name']=='credentials')
            self.assertEqual(credentials['status'], 'ok')
            self.assertNotIn('fixture-key', json.dumps(report))


class HarnessWelcomeUiTests(unittest.IsolatedAsyncioTestCase):
    def app(self, store):
        try:
            module = importlib.import_module('ui.setup')
        except ModuleNotFoundError:
            self.fail('Welcome/configuration UI is missing')
        return module.SetupApp(store=store, project_root=store.path.parent)

    async def test_cancel_welcome_does_not_create_config(self):
        with tempfile.TemporaryDirectory() as directory:
            module = importlib.import_module('nailong.core.bootstrap')
            store = module.BootstrapStore(Path(directory)/'user')
            app = self.app(store)
            async with app.run_test(size=(80, 24)) as pilot:
                self.assertIn('ignovate harness', app.query_one('#welcome-title').render().plain)
                await pilot.press('escape')
            self.assertIsNone(app.return_value)
            self.assertFalse(store.path.exists())

    async def test_existing_key_is_not_in_form_and_save_returns_launch_only_permissions(self):
        from textual.widgets import Input, OptionList
        with tempfile.TemporaryDirectory() as directory:
            from nailong.core.bootstrap import BootstrapStore
            store = BootstrapStore(Path(directory)/'user')
            store.save('https://api.deepseek.com', 'deepseek-flash', 'fixture-hidden')
            before = store.path.read_bytes()
            app = self.app(store)
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.press('enter')
                self.assertEqual(app.query_one('#setup-key', Input).value, '')
                await pilot.click('#setup-next')
                self.assertEqual(store.path.read_bytes(), before)
                await pilot.press('enter')
                app.query_one('#setup-permissions', OptionList).highlighted = 2
                await pilot.press('enter')
            self.assertEqual(app.return_value.permission_mode, 'bypassPermissions')
            self.assertNotIn('bypassPermissions', store.path.read_text())
            self.assertEqual(store.read()['provider']['api_key'], 'fixture-hidden')

    async def test_unknown_model_resets_manual_reasoning_to_default(self):
        from textual.widgets import Input, OptionList
        with tempfile.TemporaryDirectory() as directory:
            from nailong.core.bootstrap import BootstrapStore
            app = self.app(BootstrapStore(Path(directory)/'user'))
            async with app.run_test(size=(80,24)) as pilot:
                await pilot.press('enter')
                menu=app.query_one('#setup-reasoning', OptionList)
                menu.highlighted=3
                self.assertEqual(menu.get_option_at_index(menu.highlighted).id,'high')
                model=app.query_one('#setup-model', Input)
                model.value='unknown-model'

                async def wait_for_updated_choices():
                    while (menu.option_count != 1
                           or menu.get_option_at_index(0).id != 'default'):
                        await pilot.pause()

                try:
                    await asyncio.wait_for(wait_for_updated_choices(),3)
                except TimeoutError:
                    self.fail(
                        f'Model change did not update reasoning choices: model={model.value!r}, '
                        f'choices={[menu.get_option_at_index(i).id for i in range(menu.option_count)]!r}, '
                        f'highlighted={menu.highlighted!r}'
                    )
                self.assertEqual(menu.option_count,1)
                self.assertEqual(menu.get_option_at_index(menu.highlighted).id,'default')

    async def test_welcome_starts_at_left_and_keyboard_flow_never_saves_early(self):
        from textual.widgets import Input
        with tempfile.TemporaryDirectory() as directory:
            from nailong.core.bootstrap import BootstrapStore
            store=BootstrapStore(Path(directory)/'user')
            app=self.app(store)
            async with app.run_test(size=(213,56)) as pilot:
                await pilot.pause()
                self.assertLessEqual(app.query_one('#welcome-title').region.x,3)
                self.assertLessEqual(app.query_one('#welcome-title').region.y,12)
                await pilot.press('enter')
                self.assertEqual(app.step,1)
                app.query_one('#setup-key',Input).value='fixture-hidden'
                await pilot.press('enter','enter','enter')
                self.assertEqual(app.step,2)
                self.assertFalse(store.path.exists())
                await pilot.press('down','down','enter')
                self.assertEqual(app.step,3)
                self.assertFalse(store.path.exists())
                await pilot.press('escape')
                self.assertEqual(app.step,2)
                await pilot.press('ctrl+c')
            self.assertIsNone(app.return_value)
            self.assertFalse(store.path.exists())

    async def test_small_layout_keeps_fields_and_keyboard_help_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            from nailong.core.bootstrap import BootstrapStore
            from textual.widgets import Input
            app=self.app(BootstrapStore(Path(directory)/'user'))
            async with app.run_test(size=(80,24)) as pilot:
                await pilot.press('enter')
                self.assertLessEqual(app.query_one('#setup-key',Input).region.bottom,24)
                self.assertLessEqual(app.query_one('#setup-keys').region.bottom,24)
                await pilot.click('#setup-next')
                self.assertEqual(app.step,1)
                self.assertTrue(app.query_one('#setup-key-error').render().plain)

    async def test_welcome_wordmark_has_offset_double_outline(self):
        with tempfile.TemporaryDirectory() as directory:
            from nailong.core.bootstrap import BootstrapStore
            app=self.app(BootstrapStore(Path(directory)/'user'))
            async with app.run_test(size=(100,32)) as pilot:
                await pilot.pause()
                logo=app.query_one('#setup-logo').render().plain
                self.assertIn('╔',logo)
                self.assertIn('║',logo)
                self.assertIn('═',logo)
                self.assertGreaterEqual(max(len(line) for line in logo.splitlines()),70)
                self.assertLessEqual(max(len(line) for line in logo.splitlines()),96)

    async def test_wordmark_resizes_without_clipping_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            from nailong.core.bootstrap import BootstrapStore
            app=self.app(BootstrapStore(Path(directory)/'user'))
            async with app.run_test(size=(100,32)) as pilot:
                await pilot.pause()
                wide_height=len(app.query_one('#setup-logo').render().plain.splitlines())
                await pilot.resize_terminal(80,24)
                await pilot.pause()
                small=app.query_one('#setup-logo')
                self.assertTrue(small.display)
                self.assertLess(len(small.render().plain.splitlines()),wide_height)
                self.assertLessEqual(max(len(line) for line in small.render().plain.splitlines()),76)
                self.assertLessEqual(app.query_one('#setup-keys').region.bottom,24)
                await pilot.resize_terminal(50,18)
                await pilot.pause()
                self.assertFalse(app.query_one('#setup-logo').display)
                self.assertIn('ignovate harness',app.query_one('#welcome-title').render().plain)
                await pilot.press('enter')
                self.assertEqual(app.step,1)

    async def test_cli_locks_are_visible_and_retained_through_steps(self):
        from config import Settings
        from nailong.core.bootstrap import BootstrapStore
        from ui.setup import SetupApp
        with tempfile.TemporaryDirectory() as directory:
            store=BootstrapStore(Path(directory)/'user')
            settings=Settings('fixture','https://api.deepseek.com','deepseek-flash',Path(directory))
            app=SetupApp(store=store,settings=settings,locked_permission=True,
                         permission_mode='bypassPermissions',locked_reasoning='max')
            async with app.run_test(size=(80,24)) as pilot:
                await pilot.press('enter')
                await pilot.click('#setup-next')
                self.assertEqual(app.step,2)
                await pilot.press('enter')
                self.assertEqual(app.step,3)
                await pilot.press('enter')
            self.assertEqual(app.return_value.settings.reasoning_effort,'max')
            self.assertEqual(app.return_value.permission_mode,'bypassPermissions')


class HarnessSetupCliTests(unittest.TestCase):
    def test_explicit_setup_can_start_without_preexisting_model_configuration(self):
        import main
        from config import ConfigurationError, Settings
        from ui.setup import SetupResult
        settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-flash', Path.cwd())
        with patch('main._interactive_terminal', return_value=True, create=True), \
                patch('main.load_settings', side_effect=ConfigurationError('missing')), \
                patch('ui.setup.run_setup', return_value=SetupResult(settings, 'bypassPermissions')) as wizard, \
                patch('main.run_cli') as run:
            self.assertEqual(main.main(['--plain', '--setup']), 0)
        wizard.assert_called_once()
        run.assert_called_once()
        launched = run.call_args.args[0]
        self.assertEqual(launched.api_key, settings.api_key)
        self.assertEqual(launched.model, settings.model)
        self.assertEqual(launched.cli_preferences['reasoning_effort'],settings.reasoning_effort)
        self.assertEqual(launched.cli_preferences['model'],'default')
        self.assertEqual(run.call_args.kwargs, {'permission_mode':'bypassPermissions'})

    def test_cancel_never_starts_runtime_and_non_tty_skips_welcome(self):
        import main
        from config import Settings
        settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-flash', Path.cwd())
        with patch('main._interactive_terminal', return_value=True, create=True), \
                patch('main.load_settings', return_value=settings), \
                patch('ui.setup.run_setup', return_value=None), patch('main.run_cli') as run:
            self.assertEqual(main.main(['--plain', '--setup']), 0)
            run.assert_not_called()
        with patch('main._interactive_terminal', return_value=False, create=True), \
                patch('main.load_settings', return_value=settings), \
                patch('ui.setup.run_setup') as wizard, patch('main.run_cli') as run:
            self.assertEqual(main.main(['--plain']), 0)
            wizard.assert_not_called()
            run.assert_called_once_with(settings)

    def test_headless_rejects_setup_before_reading_config(self):
        import main
        from io import StringIO
        with patch('sys.stderr', new_callable=StringIO), patch('main.load_settings') as load:
            with self.assertRaises(SystemExit) as error:
                main.main(['-p', 'hello', '--setup'])
        self.assertEqual(error.exception.code, 2)
        load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
