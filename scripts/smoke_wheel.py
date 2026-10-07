"""Check the built distribution from outside the source checkout, without API calls."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='ignovate-wheel-') as directory:
        root = Path(directory)
        target = root/'installed'
        with zipfile.ZipFile(args.wheel.resolve()) as archive:
            names = archive.namelist()
            assert not any('/.env' in name or name.startswith(('.env', '.idea/', 'artifacts/')) for name in names)
            archive.extractall(target)
        env = {**os.environ, 'PYTHONPATH':str(target),
               'IGNOVATE_CONFIG_DIR':str(root/'config'), 'NAILONG_DATA_DIR':str(root/'data'),
               'DEEPSEEK_API_KEY':'', 'DEEPSEEK_BASE_URL':'', 'DEEPSEEK_MODEL':''}
        doctor = subprocess.run([sys.executable, '-m', 'nailong.cli', 'doctor',
                                 '--project', str(root), '--output-format', 'json'],
                                cwd=root, env=env, capture_output=True, text=True, timeout=30)
        if doctor.returncode not in (0, 1) or doctor.stderr.strip():
            raise RuntimeError('Installed doctor crashed: '+doctor.stderr)
        report = json.loads(doctor.stdout)
        assert report['project'] == str(root.resolve())
        assert report['network_checked'] is False
        assert any(row['name']=='textual' for row in report['checks'])
        assert not report['ok']  # Empty credentials produce a report, not a traceback.
        help_result = subprocess.run([sys.executable, '-m', 'nailong.cli', '--help'],
                                     cwd=root, env=env, capture_output=True, text=True, timeout=30)
        assert help_result.returncode == 0, help_result.stderr
        assert '--project' in help_result.stdout
        print('Wheel smoke passed: installed help, JSON doctor, dependency checks, no API requests.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
