from platform_fixtures import assert_private
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import Settings
from nailong.core.preferences import PreferenceStore
from ui.actions import CommandActions
from ui.controller import CommandController


class ModelPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = PreferenceStore(self.root, user_path=self.root/'user/preferences.json')

    def test_add_selects_and_persists_model_without_losing_other_settings(self):
        path = self.store.local_path
        path.parent.mkdir()
        path.write_text(json.dumps({'theme': 'light', 'permissions': {'allow': ['read_file']},
                                   'models': {'fast': {'model': 'existing-model'}}}), newline='\n')
        result = self.store.add_model('pro', 'new-model')
        self.assertEqual(result['model_name'], 'pro')
        self.assertEqual(result['model'], 'new-model')
        saved = json.loads(path.read_text())
        self.assertEqual(saved['permissions'], {'allow': ['read_file']})
        self.assertEqual(saved['models'], {'fast': {'model': 'existing-model'}, 'pro': {'model': 'new-model'}})
        self.assertEqual(saved['model'], 'pro')
        assert_private(self, path)
        restarted = PreferenceStore(self.root, user_path=self.store.user_path)
        self.assertEqual(restarted.effective()['model'], 'new-model')

    def test_global_add_overrides_cli_selection_and_is_available_to_other_projects(self):
        self.store.effective(cli={'model': 'default'})
        result = self.store.add_model('pro', 'new-model', global_scope=True)
        self.assertEqual(result['model'], 'new-model')
        self.assertFalse(self.store.local_path.exists())
        other = PreferenceStore(self.root/'other', user_path=self.store.user_path)
        self.assertEqual(other.effective()['model'], 'new-model')

    def test_duplicate_reserved_and_invalid_models_do_not_write(self):
        self.store.add_model('pro', 'new-model')
        before = self.store.local_path.read_bytes()
        for name, model_id in [('pro', 'replacement'), ('default', 'replacement'),
                               (' ', 'new-model'), ('bad\nname', 'new-model'),
                               ('ok', ''), ('ok', 'bad model')]:
            with self.subTest(name=name, model_id=model_id):
                with self.assertRaises(ValueError):
                    self.store.add_model(name, model_id)
                self.assertEqual(self.store.local_path.read_bytes(), before)
                self.assertEqual(self.store.effective()['model'], 'new-model')

    def test_failed_write_leaves_session_selection_and_config_unchanged(self):
        self.store.add_model('pro', 'new-model')
        before = self.store.local_path.read_bytes()
        with patch('nailong.core.preferences.atomic_json', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):
                self.store.add_model('next', 'next-model')
        self.assertEqual(self.store.local_path.read_bytes(), before)
        self.assertEqual(self.store.effective()['model_name'], 'pro')
        self.assertNotIn('next', self.store.effective()['models'])


class ModelCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-flash', root)
        self.controller = CommandController(self.settings)
        self.actions = CommandActions(self.controller)
        self.preferences = PreferenceStore(root, user_path=root/'user/preferences.json')
        self.calls = []
        self.factory = SimpleNamespace(settings=self.settings, preferences=self.preferences)
        def set_model(model_id):
            self.calls.append(model_id)
            self.factory.settings = replace(self.factory.settings, model=model_id)
        self.factory.set_model = set_model
        self.service = SimpleNamespace(runtime_factory=self.factory, session_store=None)

    async def execute(self, message, **kwargs):
        return await self.actions.execute(self.controller.resolve(message), self.service, 'same-thread', **kwargs)

    async def test_add_command_persists_and_switches_runtime_without_model_request(self):
        result = await self.execute('/model add pro new-model')
        self.assertEqual(self.calls, ['new-model'])
        self.assertEqual(result.data['model_name'], 'pro')
        self.assertTrue(result.refresh)
        self.assertFalse(result.model_requests)
        self.assertEqual(self.factory.settings.api_base, self.settings.api_base)
        self.assertEqual(self.factory.settings.api_key, self.settings.api_key)
        await self.execute('/model default')
        self.assertEqual(self.calls, ['new-model', 'deepseek-flash'])

    async def test_bare_command_uses_dialog_and_cancel_does_not_change_configuration(self):
        from ui.model_flow import ModelChoice
        seen = []
        async def dialog(preferences, *, add_only=False):
            seen.append((preferences['model_name'], add_only))
            return ModelChoice('pro', 'new-model')
        await self.execute('/model', model_dialog=dialog)
        self.assertEqual(seen, [('default', False)])
        before = self.preferences.local_path.read_bytes()
        async def cancel(*args, **kwargs):
            return None
        result = await self.execute('/model', model_dialog=cancel)
        self.assertIn('取消', result.text)
        self.assertEqual(self.preferences.local_path.read_bytes(), before)
        self.assertEqual(self.calls, ['new-model'])

    async def test_add_wizard_can_save_globally_and_existing_model_can_be_selected(self):
        from ui.model_flow import ModelChoice
        async def add(preferences, *, add_only=False):
            self.assertTrue(add_only)
            return ModelChoice('pro', 'new-model')
        await self.execute('/model add --global', model_dialog=add)
        self.assertEqual(json.loads(self.preferences.user_path.read_text())['model'], 'pro')
        async def select(*args, **kwargs):
            return ModelChoice('default')
        await self.execute('/model', model_dialog=select)
        self.assertEqual(self.calls[-1], 'deepseek-flash')

    async def test_bare_command_without_dialog_lists_models_and_add_usage(self):
        result = await self.execute('/model')
        self.assertIn('default', result.text)
        self.assertIn('/model add', result.text)
        with self.assertRaises(ValueError):
            await self.execute('/model add pro')
        self.assertFalse(self.calls)


class ModelPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_input_handoff_releases_main_composer(self):
        from ui.input_broker import InputBroker
        started, cleanup_started = asyncio.Event(), asyncio.Event()
        class Session:
            default_buffer = SimpleNamespace(text='unsent draft')
            async def prompt_async(self, label, **kwargs):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleanup_started.set()
                    await asyncio.Event().wait()
        broker = InputBroker(Session())
        main_input = asyncio.create_task(broker.prompt_async('> '))
        await asyncio.wait_for(started.wait(), 2)
        async def wizard():
            async with broker.interaction() as prompt:
                await prompt('模型名称：')
        task = asyncio.create_task(wizard())
        await asyncio.wait_for(cleanup_started.wait(), 2)
        task.cancel()
        await asyncio.gather(task, main_input, return_exceptions=True)
        self.assertTrue(broker.ready.is_set())
        self.assertEqual(broker.session.default_buffer.text, 'unsent draft')

    async def test_number_selection_and_add_form(self):
        from ui.model_flow import prompt_model_choice, ModelChoice
        preferences = {'model_name': 'default', 'model': 'original',
                       'models': {'default': {'model': 'original'}, 'fast': {'model': 'fast-model'}}}
        async def run(answers):
            answers = iter(answers)
            async def prompt(label):
                return next(answers)
            return await prompt_model_choice(preferences, prompt, lambda text: None)
        self.assertEqual(await run(['2']), ModelChoice('fast'))
        self.assertEqual(await run(['a', 'pro', 'new-model']), ModelChoice('pro', 'new-model'))
        self.assertIsNone(await run(['']))
        self.assertIsNone(await run(['a', '']))
        self.assertIsNone(await run(['a', 'pro', '']))

    async def test_invalid_selection_and_duplicate_name_allow_retry(self):
        from ui.model_flow import prompt_model_choice, ModelChoice
        preferences = {'model_name': 'default', 'model': 'original', 'models': {'default': {'model': 'original'}}}
        answers = iter(['99', 'a', 'default', 'pro', 'new-model'])
        output = []
        async def prompt(label):
            return next(answers)
        choice = await prompt_model_choice(preferences, prompt, output.append)
        self.assertEqual(choice, ModelChoice('pro', 'new-model'))
        self.assertTrue(any('无效' in text for text in output))
        self.assertTrue(any('default' in text for text in output))

    async def test_numeric_zero_model_name_is_selectable(self):
        from ui.model_flow import prompt_model_choice, ModelChoice
        preferences = {'model_name': 'default', 'model': 'original',
                       'models': {'default': {'model': 'original'}, '0': {'model': 'zero-model'}}}
        async def prompt(label):
            return '0'
        self.assertEqual(await prompt_model_choice(preferences, prompt, lambda text: None), ModelChoice('0'))


class ModelTextualTests(unittest.IsolatedAsyncioTestCase):
    async def wait_for_condition(self, pilot, condition, description):
        async def ready():
            while True:
                await pilot.pause()
                if condition():
                    return
        try:
            await asyncio.wait_for(ready(), 3)
        except TimeoutError:
            self.fail(f'Timed out waiting for {description}; '
                      f'focus={getattr(pilot.app.focused, "id", None)!r}, '
                      f'panel={pilot.app._interaction_panel!r}')

    async def wait_for_target(self, pilot, selector, *, focus_id):
        from textual.errors import NoWidget
        def ready():
            matches = pilot.app.query(selector)
            if not matches:
                return False
            target = matches[0]
            if (not target.is_attached or target.content_region.width <= 0
                    or target.content_region.height <= 0
                    or target.disabled or target.has_class('-active')
                    or getattr(pilot.app.focused, 'id', None) != focus_id):
                return False
            try:
                hit, _ = pilot.app.get_widget_at(*target.region.offset)
            except NoWidget:
                return False
            return hit is target
        await self.wait_for_condition(pilot, ready, f'visible {selector} with focus {focus_id}')

    async def wait_for_composer(self, pilot):
        composer = pilot.app.query_one('#composer')
        await self.wait_for_condition(pilot,
            lambda: (pilot.app._interaction_panel is None and not composer.disabled
                     and pilot.app.focused is composer),
            'the model panel to close and restore composer focus')

    async def test_model_menu_add_and_escape_preserve_thread(self):
        from agent import AgentRuntimeFactory
        from agent_service import AgentService
        from nailong.core.sessions import ProjectSessionStore
        from tui import TerminalAgentApp
        from ui.model_view import ModelScreen
        from textual.widgets import Input, OptionList
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as data:
            root = Path(directory)
            settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-flash', root)
            factory = AgentRuntimeFactory(settings, session_store=ProjectSessionStore(root, base_dir=data))
            app = TerminalAgentApp(AgentService(factory, session_store=factory.session_store), settings)
            async with app.run_test(size=(80, 24)) as pilot:
                thread = app.thread_id
                app._dispatch('/model')
                await self.wait_for_target(pilot, '#model-add', focus_id='model-options')
                self.assertIsInstance(app._interaction_panel, ModelScreen)
                self.assertTrue(await pilot.click('#model-add'))
                await self.wait_for_target(pilot, '#model-save', focus_id='model-name')
                app.query_one('#model-name', Input).value = 'pro'
                app.query_one('#model-id', Input).value = 'new-model'
                self.assertTrue(await pilot.click('#model-save'))
                await asyncio.wait_for(app.session_runner.wait_idle(), 3)
                await self.wait_for_composer(pilot)
                self.assertEqual(app.settings.model, 'new-model')
                self.assertEqual(app.thread_id, thread)
                before = factory.preferences.local_path.read_bytes()
                app._dispatch('/model')
                await self.wait_for_target(pilot, '#model-options', focus_id='model-options')
                await pilot.press('escape')
                await asyncio.wait_for(app.session_runner.wait_idle(), 3)
                await self.wait_for_composer(pilot)
                self.assertEqual(factory.preferences.local_path.read_bytes(), before)
                self.assertEqual(app.thread_id, thread)
                app._dispatch('/model')
                await self.wait_for_target(pilot, '#model-options', focus_id='model-options')
                app.query_one('#model-options', OptionList).highlighted = 0
                await pilot.press('enter')
                await asyncio.wait_for(app.session_runner.wait_idle(), 3)
                await self.wait_for_composer(pilot)
                self.assertEqual(app.settings.model, 'deepseek-flash')
                self.assertEqual(app.thread_id, thread)

    async def test_small_terminal_form_validation_and_cancel_do_not_write(self):
        from tui import TerminalAgentApp
        from ui.model_view import ModelScreen
        from textual.widgets import Input, Static
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-flash', Path(directory))
            app = TerminalAgentApp(SimpleNamespace(runtime_factory=None, session_store=None), settings)
            async with app.run_test(size=(60, 18)) as pilot:
                app._dispatch('/model add')
                await self.wait_for_target(pilot, '#model-save', focus_id='model-name')
                self.assertIsInstance(app._interaction_panel, ModelScreen)
                app.query_one('#model-name', Input).value = 'default'
                app.query_one('#model-id', Input).value = 'new-model'
                self.assertTrue(await pilot.click('#model-save'))
                await self.wait_for_condition(pilot,
                    lambda: (app.query_one('#model-error', Static).display
                             and 'default' in str(app.query_one('#model-error', Static).content)
                             and getattr(app.focused, 'id', None) == 'model-name'),
                    'duplicate model validation to show its message and preserve form focus')
                self.assertIn('default', str(app.query_one('#model-error', Static).content))
                self.assertFalse(app.actions.preferences.local_path.exists())
                await pilot.press('escape')
                await asyncio.wait_for(app.session_runner.wait_idle(), 3)
                await self.wait_for_composer(pilot)
                self.assertFalse(app.actions.preferences.local_path.exists())


class ModelTerminalIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_inline_real_prompts_add_and_next_turn_uses_model_in_same_thread(self):
        from agent import AgentRuntimeFactory
        from agent_service import AgentService, TurnEvent
        from nailong.core.sessions import ProjectSessionStore
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        from rich.console import Console as RichConsole
        from ui.app import run_inline
        from ui.console import Console
        from ui.prompt import build_session
        from ui.theme import Theme
        with tempfile.TemporaryDirectory() as project, tempfile.TemporaryDirectory() as data, create_pipe_input() as pipe:
            root = Path(project)
            settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-flash', root)
            factory = AgentRuntimeFactory(settings, session_store=ProjectSessionStore(root, base_dir=data))
            session = build_session(root, {}, input=pipe, output=DummyOutput(), history_path=root/'history')
            prompts = asyncio.Queue()
            original_prompt = session.prompt_async
            async def prompt(label, **kwargs):
                prompts.put_nowait(label)
                return await original_prompt(label, **kwargs)
            session.prompt_async = prompt
            requests = asyncio.Queue()
            async def stream(service, message, config, **kwargs):
                requests.put_nowait((message, config['configurable']['thread_id'], factory.settings.model))
                yield TurnEvent('final', {'text': 'fixture answer'})
            output = StringIO()
            console = Console(theme=Theme(no_color=True), console=RichConsole(file=output, no_color=True))
            async def wait_prompt(fragment):
                while fragment not in await asyncio.wait_for(prompts.get(), 3):
                    pass
                await asyncio.sleep(.02)
            with patch('ui.app.AgentRuntimeFactory', return_value=factory), patch.object(AgentService, 'stream_turn', stream):
                task = asyncio.create_task(run_inline(settings, prompt_session=session, console=console))
                try:
                    await wait_prompt('> ')
                    pipe.send_text('before\r')
                    first = await asyncio.wait_for(requests.get(), 3)
                    await wait_prompt('> ')
                    pipe.send_text('/model\r')
                    await wait_prompt('选择模型')
                    pipe.send_text('a\r')
                    await wait_prompt('模型名称')
                    pipe.send_text('pro\r')
                    await wait_prompt('模型 ID')
                    pipe.send_text('new-model\r')
                    await wait_prompt('> ')
                    pipe.send_text('after\r')
                    second = await asyncio.wait_for(requests.get(), 3)
                    self.assertEqual(first[1], second[1])
                    self.assertEqual(first[2], 'deepseek-flash')
                    self.assertEqual(second[2], 'new-model')
                    await wait_prompt('> ')
                    pipe.send_text('/exit\r')
                    await asyncio.wait_for(task, 3)
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            self.assertIn('模型配置已保存', output.getvalue())
            self.assertEqual(json.loads(factory.preferences.local_path.read_text())['models']['pro'], {'model': 'new-model'})


class ModelPlainIntegrationTests(unittest.TestCase):
    def test_real_plain_process_adds_and_switches_models_without_network(self):
        with tempfile.TemporaryDirectory() as project, tempfile.TemporaryDirectory() as data:
            result = subprocess.run([sys.executable, 'main.py', '--ui', 'plain', '--project', project],
                input='/model\na\npro\nnew-model\n/model default\n/exit\n',
                capture_output=True, text=True, timeout=15,
                env={**os.environ, 'DEEPSEEK_API_KEY': 'fixture-key',
                     'DEEPSEEK_BASE_URL': 'https://api.invalid', 'DEEPSEEK_MODEL': 'deepseek-flash',
                     'NAILONG_DATA_DIR': data, 'NO_COLOR': '1'})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('模型配置已保存', result.stdout)
            self.assertNotIn('已加入队列', result.stdout)
            saved = json.loads((Path(project)/'.nailong/settings.local.json').read_text())
            self.assertEqual(saved['models'], {'pro': {'model': 'new-model'}})
            self.assertEqual(saved['model'], 'default')

    def test_plain_injected_repl_adds_model_and_retains_thread_for_next_request(self):
        from agent import AgentRuntimeFactory
        from nailong.core.sessions import ProjectSessionStore
        import main
        with tempfile.TemporaryDirectory() as project, tempfile.TemporaryDirectory() as data:
            root = Path(project)
            settings = Settings('fixture-key', 'https://api.invalid', 'deepseek-flash', root)
            factory = AgentRuntimeFactory(settings, session_store=ProjectSessionStore(root, base_dir=data))
            runtime = SimpleNamespace(_nailong_runtime_factory=factory)
            answers = iter(['before', '/model', 'a', 'pro', 'new-model', 'after', '/exit'])
            output, calls = [], []
            def run_turn(agent, message, config, **kwargs):
                calls.append((message, config['configurable']['thread_id'], factory.settings.model))
                return 'fixture answer'
            with patch('main.create_agent_runtime', return_value=runtime), patch('main.run_turn', side_effect=run_turn):
                main.run_cli(settings, input_fn=lambda label: next(answers), output_fn=output.append)
            self.assertEqual([row[0] for row in calls], ['before', 'after'])
            self.assertEqual(calls[0][1], calls[1][1])
            self.assertEqual([row[2] for row in calls], ['deepseek-flash', 'new-model'])
            self.assertTrue(any('模型配置已保存' in text for text in output))


if __name__ == '__main__':
    unittest.main()
