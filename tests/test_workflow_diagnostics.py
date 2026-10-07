import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from nailong.core.sessions import ProjectSessionStore

class DiagnosticTests(unittest.TestCase):
    def module(self):
        try: return importlib.import_module('nailong.core.diagnostics')
        except ModuleNotFoundError: self.fail('Diagnostics missing')
    def test_offline_missing_auth_bad_config_and_no_rg(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'.nailong').mkdir(); (root/'.nailong/settings.json').write_text('{broken')
            with patch.dict(os.environ,{},clear=True),patch('shutil.which',return_value=None):
                report=self.module().diagnose(root,env={})
            text=json.dumps(report,ensure_ascii=False)
            self.assertIn('DEEPSEEK_API_KEY',text); self.assertIn('rg',text)
            self.assertTrue(any(row['status']=='error' and row['name']=='configuration' for row in report['checks']))
            self.assertFalse(report['terminal']['inline'])
    def test_status_uses_immutable_prices_and_unknown_legacy_usage(self):
        with tempfile.TemporaryDirectory() as project,tempfile.TemporaryDirectory() as data:
            store=ProjectSessionStore(project,base_dir=data)
            store.append_event('thread','usage',{'input_tokens':100,'output_tokens':50,'cache_hit_tokens':20,'model':'old',
               'price_snapshot':{'input_per_million':2,'cache_hit_per_million':1,'output_per_million':4}})
            service=SimpleNamespace(runtime_factory=SimpleNamespace(settings=SimpleNamespace(model='new',api_base='https://user:private@host.invalid/path?key=private')),
                                    session_store=store,permission_mode='default')
            report=self.module().status_snapshot(service,'thread')
            self.assertAlmostEqual(report['usage']['cost_usd'],.00038)
            self.assertNotIn('private',json.dumps(report)); self.assertEqual(report['provider_host'],'host.invalid')
            store.append_event('thread','usage',{'input_tokens':10,'output_tokens':3})
            report=self.module().status_snapshot(service,'thread')
            self.assertFalse(report['usage']['cost_complete']); self.assertIsNone(report['usage']['cost_usd'])
    def test_cli_doctor_runs_without_required_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            environment={**os.environ,'DEEPSEEK_API_KEY':'','DEEPSEEK_MODEL':'','DEEPSEEK_BASE_URL':'','IGNOVATE_CONFIG_DIR':str(Path(directory)/'user')}
            result=subprocess.run([sys.executable,'-m','nailong.cli','--doctor','--project',directory,'--output-format','json'],
                                  capture_output=True,text=True,env=environment,timeout=8)
            self.assertIn(result.returncode,(0,1)); payload=json.loads(result.stdout)
            self.assertEqual(payload['project'],str(Path(directory).resolve()))
            self.assertFalse(payload['network_checked']); self.assertNotIn('启动失败',result.stdout)
    def test_settings_reject_credential_bearing_provider_url(self):
        import config
        with tempfile.TemporaryDirectory() as directory, patch('config.load_dotenv'),patch.dict(os.environ,{'DEEPSEEK_API_KEY':'key','DEEPSEEK_MODEL':'model',
                                             'DEEPSEEK_BASE_URL':'https://user:password@host.invalid/?key=secret'},clear=True):
            with self.assertRaises(config.ConfigurationError): config.load_settings(config_path=Path(directory)/'user/config.json')

    def test_installed_diagnostics_use_distribution_dependencies_without_source_files(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed_file = root/'installed/nailong/core/diagnostics.py'
            with patch.object(module, '__file__', str(installed_file)), \
                    patch.object(module.importlib.metadata, 'requires', return_value=['textual>=8.2,<9.0']):
                report = module.diagnose(root, env={})
            dependency = next(row for row in report['checks'] if row['name']=='textual')
            self.assertEqual(dependency['status'], 'ok')
            entrypoint = next(row for row in report['checks'] if row['name']=='entrypoint')
            self.assertNotIn('pip install -e', entrypoint['repair'])

    def test_doctor_rejects_connections_that_startup_rejects_without_exposing_credentials(self):
        from nailong.core.connection import validate_connection, ConfigurationError
        invalid = [
            ('https://api.invalid:', 'model', 'fixture-secret'),
            ('https://api. invalid', 'model', 'fixture-secret'),
            ('https://api.invalid', 'bad model', 'fixture-secret'),
            ('https://api.invalid', 'model', 'fixture-secret\ninvalid'),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for base, model, key in invalid:
                with self.subTest(base=base, model=model):
                    with self.assertRaises(ConfigurationError):
                        validate_connection(base, model, key)
                    report = self.module().diagnose(directory, env={
                        'DEEPSEEK_BASE_URL':base, 'DEEPSEEK_MODEL':model, 'DEEPSEEK_API_KEY':key})
                    self.assertFalse(report['ok'])
                    self.assertNotIn('fixture-secret', json.dumps(report))
