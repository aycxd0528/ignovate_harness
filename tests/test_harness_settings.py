import importlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nailong.core.preferences import PreferenceStore


class HarnessReasoningTests(unittest.TestCase):
    def reasoning(self):
        try:
            return importlib.import_module('nailong.core.reasoning')
        except ModuleNotFoundError:
            self.fail('Shared reasoning settings are missing')

    def test_supported_options_match_provider_and_unknown_models_use_default(self):
        module = self.reasoning()
        self.assertEqual([row[0] for row in module.reasoning_options('deepseek-flash')],
                         ['default', 'none', 'low', 'high', 'max'])
        self.assertEqual([row[0] for row in module.reasoning_options('other-model')], ['default'])

    def test_wire_parameters_disable_thinking_and_support_max(self):
        module = self.reasoning()
        from langchain_deepseek import ChatDeepSeek
        from langchain_core.messages import HumanMessage
        for effort in ('none', 'low', 'high', 'max'):
            with self.subTest(effort=effort):
                model = ChatDeepSeek(model='deepseek-flash', api_key='fixture',
                                    **module.model_reasoning_kwargs('deepseek-flash', effort))
                payload = model._get_request_payload([HumanMessage(content='hello')])
                self.assertEqual(payload['reasoning_effort'], effort)
                self.assertEqual(payload['extra_body']['thinking']['type'],
                                 'disabled' if effort == 'none' else 'enabled')
        self.assertEqual(module.model_reasoning_kwargs('other-model', 'default'), {})
        with self.assertRaises(ValueError):
            module.model_reasoning_kwargs('other-model', 'high')

    def test_preference_is_persistent_and_cli_then_session_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = PreferenceStore(root, user_path=root/'user/preferences.json')
            store.effective('deepseek-flash', cli={'reasoning_effort': 'low'})
            values = store.set('reasoning_effort', 'max')
            self.assertEqual(values['reasoning_effort'], 'max')
            self.assertEqual(json.loads(store.local_path.read_text())['reasoning_effort'], 'max')
            restarted = PreferenceStore(root, user_path=store.user_path)
            self.assertEqual(restarted.effective(cli={'reasoning_effort': 'high'})['reasoning_effort'], 'high')

    def test_unsupported_combination_does_not_write_or_change_preference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = PreferenceStore(root, user_path=root/'user/preferences.json')
            store.set('reasoning_effort', 'high')
            before = store.local_path.read_bytes()
            with self.assertRaises(ValueError):
                store.add_model('unknown', 'unknown-model')
            self.assertEqual(store.local_path.read_bytes(), before)
            self.assertEqual(store.effective()['reasoning_effort'], 'high')

    def test_brand_is_consistent_in_ui_prompt_and_packaging(self):
        from nailong.core.prompts import SYSTEM_PROMPT
        from ui.presentation import render_role_header
        self.assertIn('ignovate harness', SYSTEM_PROMPT)
        self.assertIn('ignovate harness', render_role_header('assistant').plain)
        import tomllib
        project = tomllib.loads((Path(__file__).resolve().parents[1]/'pyproject.toml').read_text())
        self.assertEqual(project['project']['name'], 'ignovate-harness')
        self.assertEqual(project['project']['scripts']['ignovate'], 'nailong.cli:main')

    def test_thinking_tool_messages_preserve_required_provider_field(self):
        try:
            cls = importlib.import_module('nailong.core.model').HarnessChatDeepSeek
        except ModuleNotFoundError:
            self.fail('Thinking-compatible provider serialization is missing')
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
        model = cls(model='deepseek-flash', api_key='fixture', reasoning_effort='high')
        messages = [HumanMessage(content='read'), AIMessage(content='',
            additional_kwargs={'reasoning_content':'fixture provider field'},
            tool_calls=[{'name':'read_file','args':{'path':'a'},'id':'call-1'}]),
            ToolMessage(content='result', tool_call_id='call-1')]
        payload = model._get_request_payload(messages)
        self.assertEqual(payload['messages'][1].get('reasoning_content'), 'fixture provider field')


class HarnessSettingCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_permission_selection_changes_actual_engine_mode_without_persistence(self):
        from config import Settings
        from agent_service import AgentService
        from ui.actions import CommandActions
        from ui.controller import CommandController
        from nailong.core.permissions import PermissionEngine, Decision
        from nailong.tools.execution import ToolExecutionContext
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings('fixture', 'https://api.invalid', 'deepseek-flash', root)
            prefs = PreferenceStore(root, user_path=root/'user/preferences.json')
            execution = ToolExecutionContext(root, permission_engine=PermissionEngine(root))
            factory = SimpleNamespace(settings=settings, preferences=prefs, tool_execution_context=execution)
            service = AgentService(factory)
            actions = CommandActions(CommandController(settings))
            for choice in ('acceptEdits', 'bypassPermissions', 'default'):
                request = actions.controller.resolve('/permissions '+choice)
                self.assertTrue(actions.handles(request))
                result = await actions.execute(request, service, 'session')
                self.assertTrue(result.refresh)
                self.assertEqual(execution.permission_mode, choice)
                self.assertEqual(service.permission_mode, choice)
            self.assertEqual(execution.permission_engine.decide_action('write_file', {'path':'a.txt','content':'a'}, mode=service.permission_mode).decision, Decision.ASK)
            self.assertFalse(prefs.local_path.exists())

    async def test_reasoning_dialog_uses_same_supported_choices_and_can_cancel(self):
        from config import Settings
        from ui.actions import CommandActions
        from ui.controller import CommandController
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings('fixture', 'https://api.invalid', 'deepseek-flash', root)
            prefs = PreferenceStore(root, user_path=root/'user/preferences.json')
            seen = []
            factory = SimpleNamespace(settings=settings, preferences=prefs, set_reasoning=seen.append)
            service = SimpleNamespace(runtime_factory=factory, session_store=None)
            actions = CommandActions(CommandController(settings))
            async def select(kind, current, options):
                self.assertEqual(kind, '推理强度')
                self.assertEqual([row[0] for row in options], ['default','none','low','high','max'])
                return 'max'
            result = await actions.execute(actions.controller.resolve('/reasoning'), service, 'session', setting_dialog=select)
            self.assertEqual(seen, ['max'])
            self.assertEqual(result.data['reasoning_effort'], 'max')
            before = prefs.local_path.read_bytes()
            async def cancel(*args): return None
            await actions.execute(actions.controller.resolve('/reasoning'), service, 'session', setting_dialog=cancel)
            self.assertEqual(before, prefs.local_path.read_bytes())
            self.assertEqual(seen, ['max'])


if __name__ == '__main__':
    unittest.main()
