from platform_fixtures import assert_private
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class MCPConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        try:
            self.module = importlib.import_module('nailong.mcp.config')
        except ModuleNotFoundError:
            self.fail('MCP project configuration is not implemented')
        self.store = self.module.MCPConfigStore(self.root)

    def test_listing_does_not_create_configuration(self):
        self.assertEqual(self.store.list_servers(), {})
        self.assertFalse((self.root / '.nailong').exists())

    def test_atomic_add_remove_preserves_other_configuration(self):
        path = self.root / '.nailong/mcp.json'
        path.parent.mkdir()
        path.write_text(json.dumps({'note': 'keep', 'mcpServers': {}}), newline='\n')
        self.store.add('docs', {'transport': 'http', 'url': 'https://example.com/mcp'})
        self.store.add('local', {'transport': 'stdio', 'command': '/path with spaces/python', 'args': ['server.py']})
        assert_private(self, path)
        self.assertEqual(json.loads(path.read_text())['note'], 'keep')
        with self.assertRaises(ValueError):
            self.store.add('docs', {'transport': 'http', 'url': 'https://other.example/mcp'})
        self.store.remove('docs')
        self.assertEqual(set(self.store.list_servers()), {'local'})
        with self.assertRaises(ValueError):
            self.store.remove('absent')

    def test_invalid_definitions_never_create_a_file(self):
        cases = [
            ('../escape', {'transport': 'stdio', 'command': 'python'}),
            ('bad', {'transport': 'stdio', 'command': ''}),
            ('bad', {'transport': 'stdio', 'command': 'python', 'args': 'server.py'}),
            ('bad', {'transport': 'http', 'url': 'file:///etc/passwd'}),
            ('bad', {'transport': 'http', 'url': 'https://user:secret@example.com/mcp'}),
            ('bad', {'transport': 'http', 'url': 'https://example.com/mcp?token=secret'}),
            ('bad', {'transport': 'http', 'url': 'https://example.com/mcp', 'headers': {'Authorization': 'secret'}}),
            ('bad', {'transport': 'stdio', 'command': 'python', 'env': {'KEY': '${DEEPSEEK_API_KEY}'}}),
            ('bad', {'transport': 'stdio', 'command': 'python', 'timeout_seconds': True}),
            ('bad', {'transport': 'stdio', 'command': 'python', 'unexpected': 'x'}),
        ]
        for name, definition in cases:
            with self.subTest(definition=definition):
                with self.assertRaises(ValueError):
                    self.store.add(name, definition)
        self.assertFalse((self.root / '.nailong/mcp.json').exists())

    def test_bad_json_and_symlink_are_preserved(self):
        path = self.root / '.nailong/mcp.json'
        path.parent.mkdir()
        path.write_text('{invalid', newline='\n')
        with self.assertRaises(ValueError):
            self.store.add('docs', {'transport': 'http', 'url': 'https://example.com/mcp'})
        self.assertEqual(path.read_text(), '{invalid')
        path.unlink()
        target = self.root / 'other.json'
        target.write_text('{}', newline='\n')
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.store.list_servers()
        self.assertEqual(target.read_text(), '{}')

    def test_environment_references_resolve_only_when_explicit_and_available(self):
        with patch.dict(os.environ, {'MCP_TEST_TOKEN': 'private-token'}, clear=True):
            self.assertEqual(self.module.resolve_references({'Authorization': '${MCP_TEST_TOKEN}'}),
                             {'Authorization': 'private-token'})
            with self.assertRaises(ValueError):
                self.module.resolve_references({'Authorization': '${MCP_MISSING}'})


if __name__ == '__main__':
    unittest.main()
