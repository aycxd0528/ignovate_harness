"""Per-model provenance must survive configuration changes."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_service import AgentService
from config import Settings
from nailong.core.costs import CostEstimator
from nailong.core.permissions import PermissionEngine
from nailong.core.sessions import ProjectSessionStore


class WorkflowAccountingTests(unittest.TestCase):
    def test_usage_event_keeps_immutable_model_price_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / '.nailong/settings.json'
            config.parent.mkdir()
            config.write_text(json.dumps({'pricing': {'private': {
                'input_per_million': 1, 'cache_hit_per_million': 0.2,
                'output_per_million': 2,
            }}}), newline='\n')
            settings = Settings('secret', 'https://user:secret@example.org/api?token=secret', 'private', root)
            factory = SimpleNamespace(settings=settings)
            store = ProjectSessionStore(root, base_dir=root/'data')
            service = AgentService(factory, api_key='secret', session_store=store,
                                   permission_engine=PermissionEngine(root))
            service._event('thread', 'usage', {'input_tokens': 100, 'output_tokens': 10})
            config.write_text('{}', newline='\n')
            event = store.read_events('thread')[0]['data']
            self.assertEqual(event.get('model'), 'private')
            self.assertEqual(event.get('provider_host'), 'example.org')
            self.assertEqual(event.get('price_snapshot', {}).get('input_per_million'), 1)
            self.assertNotIn('secret', json.dumps(event))

    def test_price_snapshot_reports_unknown_without_zero_price(self):
        with tempfile.TemporaryDirectory() as directory:
            estimator = CostEstimator('unknown', directory)
            snapshot = getattr(estimator, 'snapshot', lambda: {})()
            self.assertEqual(snapshot.get('model'), 'unknown')
            self.assertIsNone(snapshot.get('input_per_million'))
            self.assertEqual(snapshot.get('source'), 'unknown')

    def test_nonfinite_or_negative_project_price_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'.nailong/settings.json'
            path.parent.mkdir()
            for value in (float('inf'), float('nan'), -1):
                with self.subTest(value=value):
                    path.write_text(json.dumps({'pricing': {'private': {
                        'input_per_million': value, 'cache_hit_per_million': 0,
                        'output_per_million': 2,
                    }}}), newline='\n')
                    self.assertFalse(CostEstimator('private', root).available)


if __name__ == '__main__':
    unittest.main()

class AccountingBoundaryTests(unittest.TestCase):
    def test_invalid_configured_builtin_price_does_not_fall_back_to_builtin(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); path=root/'.nailong/settings.json';path.parent.mkdir()
            for values in [{'input_per_million':'invalid','cache_hit_per_million':0,'output_per_million':1},
                           {'input_per_million':True,'cache_hit_per_million':0,'output_per_million':1},
                           {'input_per_million':1}]:
                path.write_text(json.dumps({'pricing':{'deepseek-flash':values}}), newline='\n')
                self.assertFalse(CostEstimator('deepseek-flash',root).available)
    def test_usage_carries_provider_reported_actual_model(self):
        from langchain_core.messages import AIMessage
        from nailong.core.usage import message_usage
        usage=message_usage(AIMessage(content='ok',usage_metadata={'input_tokens':10,'output_tokens':2,'total_tokens':12},
                                     response_metadata={'model_name':'actual-model'}))
        self.assertEqual(usage.get('model'),'actual-model')
