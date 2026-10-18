"""Release input validation must fail before creating partial desktop output."""
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']


@unittest.skipUnless(sys.platform == 'win32', 'Windows desktop build inputs')
class WindowsReleaseTests(unittest.TestCase):
    def test_incomplete_runtime_is_rejected_without_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root/f'ignovate_harness-{VERSION}-py3-none-any.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr(f'ignovate_harness-{VERSION}.dist-info/METADATA',
                                 f'Name: ignovate-harness\nVersion: {VERSION}\n')
            runtime = root/'runtime'; runtime.mkdir()
            output = root/'output'
            result = subprocess.run([sys.executable, str(ROOT/'scripts/build_windows.py'),
                '--wheel', str(wheel), '--runtime-base', str(runtime), '--output', str(output),
                '--python-standalone-build', '20260510'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('relocatable Windows Python 3.11', result.stderr)
            self.assertFalse(output.exists())
