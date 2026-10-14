"""Settings persistence uses disposable native/project configuration only."""

import importlib.util
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import Settings
from nailong.core.bootstrap import BootstrapStore
from nailong.core.preferences import PreferenceStore, atomic_json
from nailong.core.sessions import ProjectSessionStore
from nailong.gui.controller import GUIController


class GUISettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.home_patch = patch('pathlib.Path.home', return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.env_patch = patch.dict(os.environ, {
            'IGNOVATE_CONFIG_DIR': str(self.home / '.ignovate'),
            'DEEPSEEK_API_KEY': 'initial-private-key',
            'DEEPSEEK_BASE_URL': 'https://initial.example.test',
            'DEEPSEEK_MODEL': 'initial-model',
        })
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.dotenv_patch = patch('config.load_dotenv', return_value=False)
        self.dotenv_patch.start()
        self.addCleanup(self.dotenv_patch.stop)
        self.sessions = ProjectSessionStore(self.project, base_dir=self.root / 'data', api_key='initial-private-key')
        preferences = PreferenceStore(self.project)
        preferences.default_model = 'initial-model'
        self.factory = SimpleNamespace(
            settings=Settings('initial-private-key', 'https://initial.example.test', 'initial-model', self.project),
            preferences=preferences,
        )
        service = SimpleNamespace(runtime_factory=self.factory, permission_mode='default')
        self.controller = GUIController(self.project, sessions=self.sessions, service=service)
        self.addAsyncCleanup(self.controller.close)

    def settings(self, controller=None):
        self.assertIsNotNone(importlib.util.find_spec('nailong.gui.settings'), 'GUI settings module is missing')
        from nailong.gui.settings import GUISettings
        return GUISettings(controller or self.controller)

    async def save_provider(self, **changes):
        return await self.settings().provider({
            'action': 'save', 'name': 'Alternative', 'api_base': 'https://alternative.example.test/v1',
            'model': 'alternative-model', 'api_key': 'alternative-private-key', **changes,
        })

    @staticmethod
    def managed(catalog):
        return [item for item in catalog['providers'] if item['id'] != 'current']

    async def test_unsaved_defaults_are_read_only_and_preserve_appearance_migration(self):
        result = self.settings().preferences()
        self.assertEqual(result, {'preferences': {
            'default_permission': 'default', 'theme': 'system', 'font_size': 14,
            'tool_details': 'standard', 'show_changes': True, 'busy_send': 'queue',
            'shortcuts': {'new_chat': 'Mod+N', 'commands': 'Mod+K', 'find': 'Mod+F'},
        }, 'saved_keys': []})
        catalog = self.settings().catalog()
        self.assertEqual(catalog['providers'][0], {
            'id': 'current', 'name': '当前连接', 'api_base': 'https://initial.example.test',
            'model': 'initial-model', 'has_key': True, 'active': True,
        })
        self.assertEqual(set(catalog['paths']), {'preferences', 'providers', 'configuration'})
        self.assertFalse((self.project / '.nailong/gui-settings.json').exists())
        self.assertFalse(BootstrapStore().path.exists())
        self.assertFalse(Path(catalog['paths']['providers']).exists())

    async def test_project_preferences_reload_and_saved_keys_only_contain_explicit_choices(self):
        catalog = self.settings().update({'font_size': 18, 'busy_send': 'draft', 'default_permission': 'acceptEdits'})
        self.assertEqual(catalog['preferences']['font_size'], 18)
        self.assertEqual(set(catalog['saved_keys']), {'font_size', 'busy_send', 'default_permission'})
        self.assertNotIn('theme', catalog['saved_keys'])
        reloaded = self.settings().preferences()
        self.assertEqual(reloaded['preferences']['busy_send'], 'draft')
        self.assertEqual(reloaded['preferences']['default_permission'], 'acceptEdits')
        native = json.loads((self.project / '.nailong/gui-settings.json').read_text())
        self.assertEqual(native['preferences'], {'font_size': 18, 'busy_send': 'draft', 'default_permission': 'acceptEdits'})
        self.assertNotIn('initial-private-key', json.dumps(native))
        self.settings().update({'theme': 'system'})
        self.assertIn('theme', self.settings().preferences()['saved_keys'])

    async def test_general_settings_reject_unknown_fields_and_invalid_values_without_mutation(self):
        self.settings().update({'theme': 'dark'})
        path = self.project / '.nailong/gui-settings.json'
        previous = path.read_bytes()
        for changes in (
            {'default_profile': 'plan'}, {'output_style': 'detailed'}, {'unknown': 'x'},
            {'font_size': True}, {'font_size': 11}, {'font_size': 23}, {'font_size': 14.5},
            {'show_changes': 1}, {'theme': 'ansi'}, {'default_permission': 'full'},
            {'tool_details': 'all'}, {'busy_send': 'send'}, ['theme'],
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.settings().update(changes)
            self.assertEqual(path.read_bytes(), previous)

    async def test_shortcuts_validate_combinations_and_duplicate_keys(self):
        result = self.settings().update({'shortcuts': {'new_chat': 'Mod+Shift+N'}})
        self.assertEqual(result['preferences']['shortcuts'], {
            'new_chat': 'Mod+Shift+N', 'commands': 'Mod+K', 'find': 'Mod+F',
        })
        for shortcut in ({'new_chat': 'Mod+K'}, {'new_chat': 'mod+k'}, {'find': 'Ctrl+F'},
                         {'find': 'Mod+Shift+Shift+F'}, {'find': 'Mod+'}, {'delete': 'Mod+D'}):
            with self.subTest(shortcut=shortcut), self.assertRaises(ValueError):
                self.settings().update({'shortcuts': shortcut})
        self.assertEqual(self.settings().preferences()['preferences']['shortcuts']['new_chat'], 'Mod+Shift+N')

    async def test_project_settings_and_vault_reject_symlinks(self):
        outside = self.root / 'outside.json'
        outside.write_text('{"leave":"untouched"}')
        settings_path = self.project / '.nailong/gui-settings.json'
        settings_path.parent.mkdir()
        settings_path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, '链接'):
            self.settings().preferences()
        with self.assertRaisesRegex(ValueError, '链接'):
            self.settings().update({'theme': 'light'})
        settings_path.unlink()
        vault = Path(self.settings().catalog()['paths']['providers'])
        vault.parent.mkdir(parents=True, exist_ok=True)
        vault.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, '链接'):
            await self.save_provider()
        self.assertEqual(outside.read_text(), '{"leave":"untouched"}')

    async def test_corrupt_or_oversize_settings_are_not_silently_replaced(self):
        path = self.project / '.nailong/gui-settings.json'
        path.parent.mkdir()
        for content in ('{bad', '[]', json.dumps({'version': 1, 'preferences': {'font_size': 100}}), ' ' * (1024 * 1024 + 1)):
            path.write_text(content)
            with self.subTest(content=content[:60]), self.assertRaises(ValueError):
                self.settings().update({'theme': 'light'})
            self.assertEqual(path.read_text(), content)

    async def test_inactive_provider_save_is_native_only_and_does_not_change_runtime(self):
        factory = self.controller.factory
        catalog = await self.save_provider()
        provider = self.managed(catalog)[0]
        self.assertFalse(provider['active'])
        self.assertTrue(provider['has_key'])
        self.assertEqual(catalog['configuration']['model'], 'initial-model')
        self.assertIs(self.controller.factory, factory)
        self.assertFalse(BootstrapStore().path.exists())
        self.assertFalse((self.project / '.nailong/settings.local.json').exists())
        vault = Path(catalog['paths']['providers'])
        self.assertTrue(vault.is_relative_to(self.home))
        self.assertEqual(json.loads(vault.read_text())['providers'][0]['api_key'], 'alternative-private-key')
        self.assertNotIn('alternative-private-key', json.dumps(catalog))
        self.assertNotIn('initial-private-key', json.dumps(catalog))
        self.assertEqual(self.managed(self.settings().catalog())[0], provider)

    async def test_blank_key_edit_retains_stored_key_and_new_provider_requires_key(self):
        catalog = await self.save_provider()
        provider = self.managed(catalog)[0]
        edited = await self.save_provider(id=provider['id'], name='Renamed', api_key='   ')
        self.assertEqual(self.managed(edited)[0]['name'], 'Renamed')
        vault = Path(edited['paths']['providers'])
        self.assertEqual(json.loads(vault.read_text())['providers'][0]['api_key'], 'alternative-private-key')
        before = vault.read_bytes()
        with self.assertRaises(ValueError):
            await self.save_provider(name='Missing', api_key='')
        self.assertEqual(vault.read_bytes(), before)

    async def test_activation_uses_native_configuration_and_resets_project_model_and_reasoning(self):
        self.factory.preferences.add_model('Composer model', 'deepseek-flash')
        self.factory.preferences.set('reasoning_effort', 'max')
        self.factory.settings = replace(self.factory.settings, model='deepseek-flash', reasoning_effort='max')
        catalog = await self.save_provider()
        identity = self.managed(catalog)[0]['id']
        active = await self.settings().provider({'action': 'activate', 'id': identity})
        self.assertEqual(active['configuration']['model'], 'alternative-model')
        self.assertEqual(active['configuration']['reasoning_effort'], 'default')
        self.assertEqual([p['id'] for p in active['providers'] if p['active']], [identity])
        self.assertNotIn('current', [p['id'] for p in active['providers']])
        self.assertIsNone(self.controller.factory)
        self.assertEqual(BootstrapStore().read()['provider']['api_key'], 'alternative-private-key')
        project = json.loads((self.project / '.nailong/settings.local.json').read_text())
        self.assertEqual(project['model'], 'default')
        self.assertEqual(project['reasoning_effort'], 'default')
        self.assertNotIn('api_key', json.dumps(project))

    async def test_current_connection_edit_retains_native_key_and_activates_saved_row(self):
        catalog = await self.save_provider(id='current', name='Current renamed', api_base='https://replacement.example.test', api_key='')
        managed = self.managed(catalog)
        self.assertEqual(len(managed), 1)
        self.assertTrue(managed[0]['active'])
        self.assertEqual(catalog['configuration']['api_base'], 'https://replacement.example.test')
        self.assertEqual(BootstrapStore().read()['provider']['api_key'], 'initial-private-key')

    async def test_active_provider_edit_changes_connection_without_disclosing_key(self):
        catalog = await self.save_provider(activate=True)
        identity = self.managed(catalog)[0]['id']
        result = await self.save_provider(id=identity, model='replacement-model', api_key='')
        self.assertEqual(result['configuration']['model'], 'replacement-model')
        self.assertTrue(self.managed(result)[0]['active'])
        self.assertEqual(BootstrapStore().read()['provider']['api_key'], 'alternative-private-key')
        self.assertNotIn('alternative-private-key', json.dumps(result))

    async def test_active_current_uses_environment_model_even_when_composer_model_is_selected(self):
        self.factory.preferences.add_model('Composer model', 'deepseek-flash')
        self.factory.settings = replace(self.factory.settings, model='deepseek-flash', environment_model='initial-model')
        catalog = await self.save_provider(name='Initial', api_base='https://initial.example.test', model='initial-model', api_key='initial-private-key')
        self.assertTrue(self.managed(catalog)[0]['active'])
        self.assertNotIn('current', [p['id'] for p in catalog['providers']])
        self.assertEqual(catalog['configuration']['model'], 'deepseek-flash')

    async def test_busy_active_edits_and_switches_reject_before_mutation_but_inactive_save_is_allowed(self):
        catalog = await self.save_provider()
        identity = self.managed(catalog)[0]['id']
        vault = Path(catalog['paths']['providers'])
        for busy in ('queue', 'current_task'):
            with self.subTest(busy=busy):
                setattr(self.controller, busy, ['queued'] if busy == 'queue' else object())
                before = vault.read_bytes()
                for payload in (
                    {'action': 'activate', 'id': identity},
                    {'action': 'save', 'id': 'current', 'name': 'Current', 'api_base': 'https://new.example.test', 'model': 'new-model', 'api_key': ''},
                    {'action': 'save', 'name': 'New active', 'api_base': 'https://new.example.test', 'model': 'new-model', 'api_key': 'new-private-key', 'activate': True},
                ):
                    with self.assertRaisesRegex(ValueError, '运行|队列'):
                        await self.settings().provider(payload)
                    self.assertEqual(vault.read_bytes(), before)
                result = await self.save_provider(id=identity, name='Allowed inactive edit', api_key='')
                self.assertEqual(self.managed(result)[0]['name'], 'Allowed inactive edit')
                setattr(self.controller, busy, [] if busy == 'queue' else None)

    async def test_active_provider_cannot_be_removed_and_inactive_removal_persists(self):
        catalog = await self.save_provider()
        identity = self.managed(catalog)[0]['id']
        result = await self.settings().provider({'action': 'remove', 'id': identity})
        self.assertEqual(self.managed(result), [])
        catalog = await self.save_provider(activate=True)
        identity = self.managed(catalog)[0]['id']
        before = Path(catalog['paths']['providers']).read_bytes()
        with self.assertRaises(ValueError):
            await self.settings().provider({'action': 'remove', 'id': identity})
        self.assertEqual(Path(catalog['paths']['providers']).read_bytes(), before)

    async def test_duplicate_connections_keep_selected_profile_identity_after_reload(self):
        first_catalog = await self.save_provider(activate=True)
        first = self.managed(first_catalog)[0]['id']
        duplicate = await self.save_provider(name='Same connection, second profile')
        second = next(item['id'] for item in self.managed(duplicate) if item['id'] != first)
        selected = await self.settings().provider({'action': 'activate', 'id': second})
        self.assertEqual([item['id'] for item in selected['providers'] if item['active']], [second])
        self.assertEqual([item['id'] for item in self.settings().catalog()['providers'] if item['active']], [second])
        self.controller.queue.append('queued')
        edited = await self.save_provider(id=first, name='Inactive duplicate', model='other-model', api_key='')
        self.assertEqual(edited['configuration']['model'], 'alternative-model')
        removed = await self.settings().provider({'action': 'remove', 'id': first})
        self.assertEqual([item['id'] for item in removed['providers'] if item['active']], [second])
        self.controller.queue.clear()

    async def test_invalid_provider_inputs_are_bounded_and_errors_do_not_echo_values(self):
        self.settings()
        for changes in (
            {'name': 'x' * 81}, {'name': 'private-name\n'}, {'name': ''},
            {'api_base': 'https://user:private-password@example.test'},
            {'api_base': 'https://example.test/' + 'x' * 2048},
            {'model': 'private model'}, {'model': 'x' * 257},
            {'api_key': 'private-key\nembedded'}, {'api_key': 'x' * 8193},
            {'activate': 'yes'}, {'id': '../../private'}, {'extra': 'private-extra'},
        ):
            with self.subTest(changes=tuple(changes)), self.assertRaises(ValueError) as error:
                await self.save_provider(**changes)
            self.assertNotIn('private-password', str(error.exception))
            self.assertNotIn('private-key', str(error.exception))
            self.assertNotIn('private-extra', str(error.exception))
        with self.assertRaises(ValueError):
            await self.settings().provider({'action': 'activate', 'id': 'missing'})
        with self.assertRaises(ValueError):
            await self.settings().provider({'action': 'export'})
        self.assertFalse(BootstrapStore().path.exists())

    async def test_malformed_provider_actions_fail_with_controlled_errors(self):
        for payload in (None, [], {'action': []}, {'action': {}}, {'action': True},
                        {'action': 'save', 'id': ['private-identifier']}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                await self.settings().provider(payload)

    async def test_provider_activation_rejects_redirected_project_file_before_vault_mutation(self):
        catalog = await self.save_provider()
        vault = Path(catalog['paths']['providers'])
        previous = vault.read_bytes()
        outside = self.root / 'outside-settings.json'
        outside.write_text('{"leave":"untouched"}')
        (self.project / '.nailong').mkdir(exist_ok=True)
        (self.project / '.nailong/settings.local.json').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, '链接'):
            await self.save_provider(id=self.managed(catalog)[0]['id'], name='Redirect attempt', activate=True)
        self.assertEqual(vault.read_bytes(), previous)
        self.assertFalse(BootstrapStore().path.exists())
        self.assertEqual(outside.read_text(), '{"leave":"untouched"}')

    async def test_provider_failure_before_vault_write_does_not_attempt_unneeded_rollback(self):
        catalog = await self.save_provider()
        vault = Path(catalog['paths']['providers'])
        previous = vault.read_bytes()
        with patch('nailong.gui.settings.atomic_json', side_effect=OSError('private-storage-key')):
            with self.assertRaises(ValueError) as error:
                await self.save_provider(id=self.managed(catalog)[0]['id'], activate=True)
        self.assertNotIn('无法恢复', str(error.exception))
        self.assertNotIn('private-storage-key', str(error.exception))
        self.assertEqual(vault.read_bytes(), previous)
        self.assertFalse(BootstrapStore().path.exists())

    async def test_activation_rolls_back_vault_and_bootstrap_on_project_write_failure(self):
        self.settings()
        BootstrapStore().save('https://initial.example.test', 'initial-model', 'initial-private-key')
        project_path = self.project / '.nailong/settings.local.json'
        atomic_json(project_path, {'theme': 'light'}, self.project)
        catalog = await self.save_provider()
        vault = Path(catalog['paths']['providers'])
        prior_vault, prior_bootstrap, prior_project = vault.read_bytes(), BootstrapStore().path.read_bytes(), project_path.read_bytes()
        original = atomic_json

        def fail_project(path, value, boundary):
            if path == project_path and value.get('model') == 'default':
                raise OSError('private-storage-error-with-alternative-private-key')
            return original(path, value, boundary)

        with patch('nailong.core.preferences.atomic_json', side_effect=fail_project):
            with self.assertRaises(ValueError) as error:
                await self.save_provider(id=self.managed(catalog)[0]['id'], model='changed-model', api_key='', activate=True)
        self.assertNotIn('alternative-private-key', str(error.exception))
        self.assertEqual(vault.read_bytes(), prior_vault)
        self.assertEqual(BootstrapStore().path.read_bytes(), prior_bootstrap)
        self.assertEqual(project_path.read_bytes(), prior_project)
        self.assertEqual(self.controller.configuration()['model'], 'initial-model')
        self.assertEqual(self.sessions.api_key, 'initial-private-key')

    async def test_vault_storage_failure_does_not_change_active_connection(self):
        self.settings()
        with patch('nailong.gui.settings.atomic_json', side_effect=OSError('initial-private-key')):
            with self.assertRaises(ValueError) as error:
                await self.save_provider(activate=True)
        self.assertNotIn('initial-private-key', str(error.exception))
        self.assertEqual(self.controller.configuration()['model'], 'initial-model')
        self.assertFalse(BootstrapStore().path.exists())

    async def test_corrupt_vault_and_redirected_parent_are_rejected(self):
        vault = Path(self.settings().catalog()['paths']['providers'])
        vault.parent.mkdir(parents=True)
        for content in ('{bad', json.dumps({'version': 1, 'providers': [{'id': 'same', 'api_key': 'private-corrupt-key'}]})):
            vault.write_text(content)
            with self.assertRaises(ValueError) as error:
                self.settings().catalog()
            self.assertNotIn('private-corrupt-key', str(error.exception))
            with self.assertRaises(ValueError):
                await self.save_provider()
            self.assertEqual(vault.read_text(), content)
        vault.unlink()
        vault.parent.rmdir()
        outside = self.root / 'outside'
        outside.mkdir()
        vault.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '链接'):
            self.settings().catalog()
        self.assertEqual(list(outside.iterdir()), [])
