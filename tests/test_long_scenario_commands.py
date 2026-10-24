"""Composite acceptance fixtures use the same native command transport as other tests."""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class LongScenarioCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_composite_scenarios_build_native_commands(self):
        class CapturedSettings(Exception):
            pass
        write_text=Path.write_text
        for name,entry in (('long_task_scenario','run_composite_scenario'),
                           ('long_goal_driver_scenario','run_goal_driver_scenario')):
            with self.subTest(scenario=name),tempfile.TemporaryDirectory() as directory:
                scenario=importlib.import_module(name)
                captured={}
                def capture(path,text,*args,**kwargs):
                    if path.name=='settings.json':
                        captured.update(json.loads(text))
                        raise CapturedSettings()
                    return write_text(path,text,*args,**kwargs)
                with patch.object(scenario,'shell_join',return_value='NATIVE-FIXTURE-COMMAND',create=True) as build, \
                        patch.object(Path,'write_text',autospec=True,side_effect=capture):
                    with self.assertRaises(CapturedSettings):
                        await getattr(scenario,entry)(Path(directory))
                self.assertEqual(captured['verification']['steps'][0]['command'],'NATIVE-FIXTURE-COMMAND')
                build.assert_called_once()
                argv=build.call_args.args[0]
                self.assertEqual(argv[:3],[sys.executable,'-B','-c'])
                self.assertIn("ns['MODE']",argv[3])
                self.assertIn('report.csv',argv[3])
