"""Exercise the registered tools against real native Windows APIs."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import local_tools
from nailong.tools.files import FileSession
from nailong.core.preferences import atomic_json, read_config


@unittest.skipUnless(os.name == 'nt', 'native Windows integration')
class NativeToolsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='ignovate native tools ')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.session = FileSession(self.root)

    def test_utf8_create_read_edit_glob_and_stale_version(self):
        self.assertTrue(self.session.write_file('nested/你好.py', 'first = "你好"\n')['ok'])
        read = self.session.read_file('nested/你好.py')
        self.assertTrue(read['ok'], read)
        self.assertIn('你好', read['content'])
        edit = self.session.edit_file('nested/你好.py', 'first', 'second')
        self.assertTrue(edit['ok'], edit)
        self.assertEqual(self.session.glob('nested/*.py')['files'], ['nested/你好.py'])
        self.assertTrue(self.session.read_file('nested/你好.py')['ok'])
        (self.root/'nested/你好.py').write_text('externally changed', encoding='utf-8')
        self.assertFalse(self.session.write_file('nested/你好.py', 'replacement')['ok'])

    def test_literal_and_native_ripgrep_regex(self):
        (self.root/'hello.txt').write_text('你好 123\nnext\n', encoding='utf-8')
        literal = self.session.search_text('你好')
        self.assertTrue(literal['ok'], literal)
        self.assertTrue(literal['matches'], literal)
        self.assertIsNotNone(shutil.which('rg'), 'native release must supply ripgrep')
        result = self.session.grep(r'\d+', pattern_mode='regex')
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['matches'], result)

    def test_protected_ads_device_and_junction_paths(self):
        for path in ('.env', '.git/config', 'hello.txt:secret', 'CON', r'\\?\C:\Windows\win.ini'):
            self.assertFalse(self.session.read_file(path)['ok'], path)
        outside = self.root/'outside'
        outside.mkdir()
        (outside/'hidden.txt').write_text('hidden', encoding='utf-8')
        junction = self.root/'linked'
        subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(junction),str(outside)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: os.rmdir(junction) if junction.exists() else None)
        self.assertFalse(self.session.read_file('linked/hidden.txt')['ok'])
        self.assertNotIn('linked/hidden.txt', self.session.list_files()['files'])

    def test_config_round_trip_and_readonly_edit_preservation(self):
        config = self.root/'.nailong/settings.json'
        atomic_json(config, {'theme':'light'}, self.root)
        self.assertEqual(read_config(config, self.root), {'theme':'light'})
        target = self.root/'readonly.txt'
        target.write_text('original', encoding='utf-8')
        self.assertTrue(self.session.read_file('readonly.txt')['ok'])
        target.chmod(0o444)
        self.addCleanup(lambda: target.chmod(0o666))
        self.assertFalse(self.session.edit_file('readonly.txt','original','changed')['ok'])
        self.assertEqual(target.read_text(encoding='utf-8'), 'original')

    def test_powershell_cwd_utf8_exit_and_timeout(self):
        with local_tools.use_project_root(self.root):
            result = local_tools.run_command('[Console]::WriteLine("你好"); (Get-Location).Path; exit 7')
            self.assertEqual(result['exit_code'], 7, result)
            self.assertIn('你好', result['output'])
            self.assertIn(str(self.root), result['output'])
            timed = local_tools.run_command('Start-Sleep -Seconds 20', timeout_seconds=1)
            self.assertTrue(timed['timed_out'], timed)
            self.assertFalse(timed['ok'])


if __name__ == '__main__':
    unittest.main()
