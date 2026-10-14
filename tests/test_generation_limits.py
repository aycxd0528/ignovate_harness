"""Keep reasoning allowance in the serialized main request, including switches."""
import json
import tempfile
import unittest
from pathlib import Path

from langchain.agents.middleware import ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage

from agent import AgentRuntimeFactory
from config import Settings
from nailong.core.context import UsageCalibration
from nailong.core.context_runtime import ContextManagerMiddleware
from nailong.core.sessions import ProjectSessionStore


class GenerationLimitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'project'
        self.root.mkdir()
        self.sessions = ProjectSessionStore(self.root, base_dir=Path(temporary.name) / 'private')

    def factory(self, effort='max'):
        factory = AgentRuntimeFactory(Settings('fake-key', 'https://example.invalid',
            'deepseek-flash', self.root,
            cli_preferences={'model': 'default', 'reasoning_effort': effort}), session_store=self.sessions)
        self.addCleanup(factory.close)
        return factory

    def test_max_reasoning_uses_documented_default_in_actual_provider_payload(self):
        factory = self.factory()
        manager = ContextManagerMiddleware(factory.settings, [], 'BASE', 'chat', 'one',
            self.sessions, None, {}, UsageCalibration(), requested_output=factory.model.max_tokens)
        prepared, report = manager._prepare_request(ModelRequest(model=factory.model,
            messages=[HumanMessage(content='Give a plan')], tools=[], system_message=SystemMessage(content='BASE')))
        payload = factory.model._get_request_payload(prepared.messages, **prepared.model_settings)
        self.assertEqual(payload['max_tokens'], 131072)
        self.assertEqual(payload['reasoning_effort'], 'max')
        self.assertEqual(payload['extra_body']['thinking'], {'type': 'enabled'})
        self.assertEqual(report['output_reserve'], 131072)

    def test_reasoning_switch_recomputes_output_allowance(self):
        factory = self.factory('none')
        factory.set_reasoning('max')
        self.assertEqual(factory.model._get_request_payload('plan')['max_tokens'], 131072)
        factory.set_reasoning('high')
        self.assertEqual(factory.model._get_request_payload('plan')['max_tokens'], 65536)

    def test_documented_profiles_reach_sdk_after_each_native_switch(self):
        factory = self.factory()
        for alias in ('deepseek-flash', 'deepseek-v4-flash', 'deepseek-v4-pro'):
            factory.set_model(alias)
            for effort, expected in (('default', 65536), ('low', 65536),
                                     ('high', 65536), ('max', 131072), ('none', 8192)):
                with self.subTest(model=alias, effort=effort):
                    factory.set_reasoning(effort)
                    self.assertEqual(factory.model._get_request_payload('plan')['max_tokens'], expected)
        factory.set_reasoning('default')
        factory.set_model('unknown-model')
        self.assertEqual(factory.model._get_request_payload('plan')['max_tokens'], 4096)

    def test_project_output_cap_survives_model_and_reasoning_switch(self):
        directory = self.root / '.nailong'
        directory.mkdir()
        (directory/'settings.json').write_text(json.dumps({'generation': {'max_output_tokens': 12000}}))
        factory = self.factory()
        factory.set_model('deepseek-v4-pro')
        factory.set_reasoning('high')
        self.assertEqual(factory.model._get_request_payload('plan')['max_tokens'], 12000)

    def test_known_small_context_clips_configured_output_and_reports_it(self):
        directory = self.root / '.nailong'
        directory.mkdir()
        (directory/'settings.json').write_text(json.dumps({'context_windows': {'deepseek-flash': 8192}}))
        factory = self.factory()
        manager = ContextManagerMiddleware(factory.settings, [], 'BASE', 'chat', 'one',
            self.sessions, None, {}, UsageCalibration(), requested_output=factory.model.max_tokens)
        prepared, report = manager._prepare_request(ModelRequest(model=factory.model,
            messages=[HumanMessage(content='plan')], tools=[], system_message=SystemMessage(content='BASE')))
        self.assertEqual(prepared.model_settings['max_tokens'], 2048)
        self.assertEqual(report['requested_output_tokens'], 131072)
        self.assertTrue(report['output_clipped'])

    def test_invalid_output_cap_fails_before_provider_construction(self):
        directory = self.root / '.nailong'
        directory.mkdir()
        for value in (True, 0, -1, '32768'):
            with self.subTest(value=value):
                (directory/'settings.json').write_text(json.dumps({'generation': {'max_output_tokens': value}}))
                with self.assertRaises(ValueError):
                    self.factory()
