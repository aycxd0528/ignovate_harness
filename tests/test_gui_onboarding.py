"""First-run guide state is native, installation-wide, and never contains secrets."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nailong.core.bootstrap import BootstrapStore
from nailong.core.sessions import ProjectSessionStore
from nailong.gui.controller import GUIController
from nailong.gui.runtime_sources import RuntimeSources


class GUIOnboardingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.code = self.root / 'runtime'
        self.code.mkdir()
        (self.code / 'agent.py').write_text('version = 1\n')
        home = patch('pathlib.Path.home', return_value=self.home)
        home.start()
        self.addCleanup(home.stop)
        env = patch.dict(os.environ, {
            'HOME': str(self.home), 'IGNOVATE_CONFIG_DIR': str(self.home / '.ignovate'),
            'NAILONG_DATA_DIR': str(self.root / 'data'),
        }, clear=True)
        env.start()
        self.addCleanup(env.stop)
        dotenv = patch('config.load_dotenv', return_value=False)
        dotenv.start()
        self.addCleanup(dotenv.stop)
        self.controller = self.make_controller()
        self.addAsyncCleanup(self.controller.close)
        self.marker = BootstrapStore().path.with_name('onboarding.json')

    def make_controller(self, project=None):
        project = project or self.project
        return GUIController(project, sessions=ProjectSessionStore(project),
                             runtime_sources=RuntimeSources(self.code))

    def configure(self):
        os.environ.update(DEEPSEEK_API_KEY='private-first-run-key',
                          DEEPSEEK_BASE_URL='https://connection.example.test/v1',
                          DEEPSEEK_MODEL='first-run-model')

    def onboarding(self, controller=None):
        snapshot = (controller or self.controller).snapshot()
        self.assertIn('onboarding', snapshot, 'Native snapshot must expose first-run guide state')
        return snapshot['onboarding']

    async def test_fresh_snapshot_is_pending_without_writing_native_configuration(self):
        self.assertEqual(self.onboarding(), {
            'version': 1, 'status': 'pending', 'required': True, 'workspace_placeholder': False,
        })
        self.assertFalse(self.controller.configuration()['configured'])
        self.assertFalse(self.marker.parent.exists())
        self.assertIsNone(self.controller.factory)

    async def test_existing_connection_still_gets_guide_without_reusing_old_setup_flag(self):
        BootstrapStore().save('https://connection.example.test', 'first-run-model', 'private-first-run-key')
        self.assertTrue(BootstrapStore().completed())
        self.assertTrue(self.controller.configuration()['configured'])
        self.assertEqual(self.onboarding()['status'], 'pending')
        self.assertTrue(self.onboarding()['required'])
        self.assertFalse(self.marker.exists())

    async def test_complete_requires_connection_and_preserves_pending_on_refusal(self):
        with self.assertRaises(ValueError):
            await self.controller.command('onboarding', {'action': 'complete'})
        self.assertEqual(self.onboarding()['status'], 'pending')
        self.assertFalse(self.marker.exists())

    async def test_complete_persists_across_restart_and_workspace_switch_without_secrets(self):
        self.configure()
        project_id = self.controller.sessions.root.name
        result = await self.controller.command('onboarding', {'action': 'complete', 'project_id': project_id})
        self.assertEqual(result, {'version': 1, 'status': 'completed', 'required': False, 'workspace_placeholder': False})
        self.assertEqual(json.loads(self.marker.read_text()), {'version': 1, 'status': 'completed'})
        self.assertFalse(BootstrapStore().path.exists())
        self.assertFalse(BootstrapStore().completed())
        self.assertNotIn('private-first-run-key', json.dumps(self.controller.snapshot()))
        self.assertNotIn(str(self.project), self.marker.read_text())
        reloaded = self.make_controller()
        try:
            self.assertEqual(self.onboarding(reloaded), result)
        finally:
            await reloaded.close()
        other = self.root / 'other'
        other.mkdir()
        switched = await self.controller.command('open_project', {'path': str(other)})
        self.assertEqual(switched['onboarding'], result)
        self.assertIsNone(self.controller.factory)

    async def test_skip_without_connection_persists_without_claiming_configuration_ready(self):
        os.environ['IGNOVATE_DESKTOP_WELCOME_PROJECT'] = str(self.project)
        result = await self.controller.command('onboarding', {'action': 'skip'})
        self.assertEqual(result, {'version': 1, 'status': 'skipped', 'required': False, 'workspace_placeholder': True})
        self.assertFalse(self.controller.configuration()['configured'])
        self.assertFalse(BootstrapStore().path.exists())
        self.assertEqual(json.loads(self.marker.read_text()), {'version': 1, 'status': 'skipped'})
        reloaded = self.make_controller()
        try:
            self.assertEqual(self.onboarding(reloaded), result)
        finally:
            await reloaded.close()

    async def test_placeholder_uses_resolved_equality_and_cannot_complete_with_connection(self):
        self.configure()
        alias = self.root / 'welcome-alias'
        alias.symlink_to(self.project, target_is_directory=True)
        os.environ['IGNOVATE_DESKTOP_WELCOME_PROJECT'] = str(alias)
        self.assertTrue(self.onboarding()['workspace_placeholder'])
        with self.assertRaises(ValueError):
            await self.controller.command('onboarding', {'action': 'complete'})
        self.assertFalse(self.marker.exists())
        child = self.project / 'real-work'
        child.mkdir()
        await self.controller.command('open_project', {'path': str(child)})
        self.assertFalse(self.onboarding()['workspace_placeholder'])
        self.assertEqual((await self.controller.command('onboarding', {'action': 'complete'}))['status'], 'completed')

    async def test_completed_installation_requests_recovery_in_placeholder_but_skip_dismisses(self):
        self.configure()
        await self.controller.command('onboarding', {'action': 'complete'})
        os.environ['IGNOVATE_DESKTOP_WELCOME_PROJECT'] = str(self.project)
        self.assertEqual(self.onboarding(), {'version': 1, 'status': 'completed', 'required': True, 'workspace_placeholder': True})
        await self.controller.command('onboarding', {'action': 'skip'})
        self.assertFalse(self.onboarding()['required'])

    async def test_malformed_actions_and_stale_project_do_not_mutate_marker(self):
        await self.controller.command('onboarding', {'action': 'skip'})
        previous = self.marker.read_bytes()
        for payload in (None, [], {}, {'action': []}, {'action': {}}, {'action': True},
                        {'action': 'pending'}, {'action': 'skip', 'path': 'private-path'},
                        {'action': 'skip', 'api_key': 'private-payload-key'},
                        {'action': 'skip', 'workspace_placeholder': False},
                        {'action': 'skip', 'project_id': 'stale-project'}):
            with self.subTest(payload=payload), self.assertRaises(ValueError) as error:
                await self.controller.command('onboarding', payload)
            self.assertNotIn('private-', str(error.exception))
            self.assertEqual(self.marker.read_bytes(), previous)

    async def test_marker_corruption_is_visible_safe_and_never_silently_overwritten(self):
        self.marker.parent.mkdir()
        for content in ('{private-corrupt-key', '[]', '{}',
                        '{"version":true,"status":"completed"}',
                        '{"version":2,"status":"completed"}',
                        '{"version":1,"status":[]}',
                        '{"version":1,"status":"pending"}',
                        '{"version":1,"status":"skipped","api_key":"private-corrupt-key"}',
                        ' ' * (1024 * 1024 + 1)):
            self.marker.write_text(content)
            with self.subTest(content=content[:70]):
                state = self.onboarding()
                self.assertEqual(state['status'], 'pending')
                self.assertTrue(state['required'])
                self.assertTrue(state['error'])
                self.assertNotIn('private-', json.dumps(state))
                with self.assertRaises(ValueError) as error:
                    await self.controller.command('onboarding', {'action': 'skip'})
                self.assertNotIn('private-', str(error.exception))
                self.assertEqual(self.marker.read_text(), content)

    async def test_symlink_file_or_parent_cannot_redirect_native_state(self):
        outside = self.root / 'outside.json'
        outside.write_text('{"version":1,"status":"completed"}')
        self.marker.parent.mkdir()
        self.marker.symlink_to(outside)
        state = self.onboarding()
        self.assertEqual(state['status'], 'pending')
        self.assertIn('error', state)
        with self.assertRaises(ValueError):
            await self.controller.command('onboarding', {'action': 'skip'})
        self.assertEqual(json.loads(outside.read_text())['status'], 'completed')
        self.marker.unlink()
        self.marker.parent.rmdir()
        outside_dir = self.root / 'outside-dir'
        outside_dir.mkdir()
        self.marker.parent.symlink_to(outside_dir, target_is_directory=True)
        self.assertIn('error', self.onboarding())
        with self.assertRaises(ValueError):
            await self.controller.command('onboarding', {'action': 'skip'})
        self.assertEqual(list(outside_dir.iterdir()), [])

    async def test_source_guard_and_command_lock_apply_before_persistence(self):
        async with self.controller.command_lock:
            command = asyncio.create_task(self.controller.command('onboarding', {'action': 'skip'}))
            await asyncio.sleep(0)
            self.assertFalse(command.done())
            self.assertFalse(self.marker.exists())
        self.assertEqual((await command)['status'], 'skipped')
        previous = self.marker.read_bytes()
        (self.code / 'agent.py').write_text('version = 2\n')
        with self.assertRaisesRegex(ValueError, '更新.*重新启动'):
            await self.controller.command('onboarding', {'action': 'skip'})
        self.assertEqual(self.marker.read_bytes(), previous)

    async def test_atomic_write_failure_keeps_previous_marker_and_hides_error_values(self):
        await self.controller.command('onboarding', {'action': 'skip'})
        self.configure()
        previous = self.marker.read_bytes()
        with patch('nailong.core.preferences.os.replace', side_effect=OSError('private-storage-key')):
            with self.assertRaises(ValueError) as error:
                await self.controller.command('onboarding', {'action': 'complete'})
        self.assertNotIn('private-storage-key', str(error.exception))
        self.assertEqual(self.marker.read_bytes(), previous)
        self.assertEqual([item.name for item in self.marker.parent.iterdir()], ['onboarding.json'])
        self.assertEqual(self.onboarding()['status'], 'skipped')
