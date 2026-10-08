"""Exercise release artifacts and installed commands outside the checkout."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT/"pyproject.toml").read_text())["project"]["version"]


class ReleaseInstallTests(unittest.TestCase):
    def test_set_up_alias_reports_terminal_requirement_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, 'PYTHONPATH': str(ROOT), 'IGNOVATE_CONFIG_DIR': directory}
            result = subprocess.run([sys.executable, '-m', 'nailong.cli', 'set', 'up'],
                                    cwd=directory, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('交互终端', result.stdout + result.stderr)

    def build_bundle(self, directory):
        wheel = Path(directory) / f'ignovate_harness-{VERSION}-py3-none-any.whl'
        with zipfile.ZipFile(wheel, 'w') as archive:
            archive.writestr(f'ignovate_harness-{VERSION}.dist-info/METADATA',
                             f'Metadata-Version: 2.4\nName: ignovate-harness\nVersion: {VERSION}\n')
        output = Path(directory) / 'dist'
        result = subprocess.run([sys.executable, str(ROOT/'scripts/build_release.py'),
                                 '--wheel', str(wheel), '--output', str(output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return output

    def test_bundles_include_bootstrap_and_checksums_without_local_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.build_bundle(directory)
            with tarfile.open(output/f'ignovate-{VERSION}-unix.tar.gz') as archive:
                names = archive.getnames()
                self.assertIn(f'ignovate-{VERSION}/install.sh', names)
                self.assertIn(f'ignovate-{VERSION}/launch.sh', names)
                self.assertIn(f'ignovate-{VERSION}/requirements-release.lock', names)
                self.assertFalse(any('.env' in name or '.idea' in name for name in names))
                self.assertTrue(archive.getmember(f'ignovate-{VERSION}/install.sh').mode & 0o100)
            with zipfile.ZipFile(output/f'ignovate-{VERSION}-windows.zip') as archive:
                self.assertIn(f'ignovate-{VERSION}/install.ps1', archive.namelist())
                self.assertIn(f'ignovate-{VERSION}/launch.ps1', archive.namelist())
                self.assertIn(f'ignovate-{VERSION}/common.ps1', archive.namelist())
            for row in (output/'SHA256SUMS').read_text().splitlines():
                digest, name = row.split('  ')
                self.assertEqual(hashlib.sha256((output/name).read_bytes()).hexdigest(), digest)

    @unittest.skipIf(os.name == 'nt', 'POSIX launcher; native PowerShell smoke covers Windows')
    def test_install_without_python_preserves_config_and_dispatches_arguments(self):
        with tempfile.TemporaryDirectory(prefix="ignovate space ' ") as directory:
            output = self.build_bundle(directory)
            with tarfile.open(output/f'ignovate-{VERSION}-unix.tar.gz') as archive:
                archive.extractall(directory, filter='data')
            package = Path(directory)/f'ignovate-{VERSION}'
            install_home = Path(directory)/'data'
            bin_dir = Path(directory)/'bin'
            config = Path(directory)/'config.json'
            config.write_text('{"keep": true}')
            env = {**os.environ, 'IGNOVATE_INSTALL_HOME': str(install_home),
                   'IGNOVATE_BIN_DIR': str(bin_dir), 'IGNOVATE_NO_MODIFY_PATH': '1'}
            result = subprocess.run(['sh', str(package/'install.sh')], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read_text(), '{"keep": true}')
            self.assertFalse((install_home/'venvs').exists())
            # A pre-existing healthy runtime must start offline and preserve argv/cwd.
            runtime = install_home/f'venvs/{VERSION}'
            (runtime/'bin').mkdir(parents=True)
            (runtime/'.ready').write_text(VERSION+'\n')
            python = runtime/'bin/python'
            python.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$@"\n')
            python.chmod(0o755)
            result = subprocess.run([str(bin_dir/'ignovate'), '-p', 'a prompt with spaces'],
                                    cwd=directory, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), [str(Path(directory).resolve()), '-m', 'nailong.cli',
                                                          '-p', 'a prompt with spaces'])

    @unittest.skipIf(os.name == 'nt', 'POSIX launcher')
    def test_corrupt_bundle_is_rejected_before_installing_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.build_bundle(directory)
            with tarfile.open(output/f'ignovate-{VERSION}-unix.tar.gz') as archive:
                archive.extractall(directory, filter='data')
            package = Path(directory)/f'ignovate-{VERSION}'
            (package/'requirements-release.lock').write_text('tampered')
            bin_dir = Path(directory)/'bin'
            env = {**os.environ, 'IGNOVATE_INSTALL_HOME': str(Path(directory)/'data'),
                   'IGNOVATE_BIN_DIR': str(bin_dir), 'IGNOVATE_NO_MODIFY_PATH': '1'}
            result = subprocess.run(['sh', str(package/'install.sh')], env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((bin_dir/'ignovate').exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX launcher')
    def test_failed_repair_clears_ready_marker_and_releases_setup_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.build_bundle(directory)
            with tarfile.open(output/f'ignovate-{VERSION}-unix.tar.gz') as archive:
                archive.extractall(directory, filter='data')
            package = Path(directory)/f'ignovate-{VERSION}'
            install_home = Path(directory)/'data'
            runtime = install_home/f'venvs/{VERSION}'
            (runtime/'bin').mkdir(parents=True)
            (runtime/'.ready').write_text(VERSION+'\n')
            python = runtime/'bin/python'
            python.write_text('#!/bin/sh\nexit 0\n')
            python.chmod(0o755)
            tools = install_home/'tools'
            tools.mkdir()
            uv = tools/'uv'
            uv.write_text('#!/bin/sh\nprintf "download failed\\n" >&2\nexit 69\n')
            uv.chmod(0o755)
            result = subprocess.run(['sh', str(package/'launch.sh'), 'set', 'up', '--environment-only'],
                                    env={**os.environ, 'IGNOVATE_INSTALL_HOME': str(install_home)},
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('download failed', result.stderr)
            self.assertFalse((runtime/'.ready').exists())
            self.assertFalse((install_home/'setup.lock').exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX bash login')
    def test_launcher_is_discoverable_when_bash_login_profile_already_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.build_bundle(directory)
            with tarfile.open(output/f'ignovate-{VERSION}-unix.tar.gz') as archive:
                archive.extractall(directory, filter='data')
            root = Path(directory)
            home = root/'home'
            home.mkdir()
            (home/'.bash_profile').write_text('export PATH=/usr/bin:/bin\n')
            env = {**os.environ, 'HOME': str(home), 'ZDOTDIR': str(home),
                   'IGNOVATE_INSTALL_HOME': str(root/'data'), 'IGNOVATE_BIN_DIR': str(root/'bin'),
                   'IGNOVATE_NO_MODIFY_PATH': '0'}
            for _ in range(2):
                result = subprocess.run(['sh', str(root/f'ignovate-{VERSION}/install.sh')],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(['/bin/bash', '--login', '-c', 'command -v ignovate'],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()).resolve(), (root/'bin/ignovate').resolve())
            self.assertEqual((home/'.bash_profile').read_text().count('# ignovate PATH'), 1)


if __name__ == '__main__':
    unittest.main()
