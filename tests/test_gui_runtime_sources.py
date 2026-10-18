import tempfile
import unittest
from pathlib import Path


class RuntimeSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'agent.py').write_text('original = 1\n')
        (self.root / 'nailong/tools').mkdir(parents=True)
        (self.root / 'nailong/tools/registry.py').write_text('def build_tool_specs(): pass\n')

    def snapshot(self):
        from nailong.gui.runtime_sources import RuntimeSources
        return RuntimeSources(self.root)

    def test_python_interface_change_is_detected_before_a_late_import(self):
        sources = self.snapshot()
        self.assertFalse(sources.changed())
        (self.root / 'nailong/tools/registry.py').write_text('def build_tool_specs(*, capability_catalog=None): pass\n')
        self.assertTrue(sources.changed())
        self.assertTrue(sources.changed(), 'a later check must not accept a changed revision')

    def test_adding_or_removing_runtime_modules_requires_restart(self):
        for operation in ('add', 'remove'):
            with self.subTest(operation=operation):
                sources = self.snapshot()
                if operation == 'add':
                    (self.root / 'nailong/tools/extra.py').write_text('new = True\n')
                else:
                    (self.root / 'agent.py').unlink()
                self.assertTrue(sources.changed())

    def test_same_content_touch_and_project_preferences_do_not_require_restart(self):
        sources = self.snapshot()
        (self.root / 'agent.py').touch()
        (self.root / 'nailong/settings.json').write_text('{"reasoning_effort":"high"}')
        (self.root / 'README.md').write_text('Updated documentation')
        self.assertFalse(sources.changed())
