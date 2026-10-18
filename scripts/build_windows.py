"""Build a self-contained Windows x64 desktop ZIP containing Ignovate.exe."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_macos import run, standalone_build_id, validate_wheel


def build_windows(wheel: Path, output: Path, runtime_base: Path, ripgrep: Path,
                  electron_zip_dir: Path | None = None,
                  python_standalone_build: str | None = None) -> Path:
    if sys.platform != 'win32':
        raise ValueError('Build the Windows desktop release on Windows x64')
    version = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    validate_wheel(wheel, version)
    build_id = standalone_build_id(runtime_base, python_standalone_build)
    if not all((runtime_base/name).exists() for name in ('python.exe', 'python311.dll', 'Lib', 'LICENSE.txt')):
        raise ValueError('A relocatable Windows Python 3.11 distribution is required')
    probe = json.loads(subprocess.check_output([str(runtime_base/'python.exe'), '-I', '-B', '-c',
        'import json,struct,sys;print(json.dumps([list(sys.version_info[:2]),struct.calcsize("P")]))'], text=True))
    if probe != [[3, 11], 8]:
        raise ValueError('A relocatable Windows Python 3.11 x64 distribution is required')
    notices = ROOT/'desktop/licenses'/('python-standalone-'+build_id)
    if not notices.is_dir():
        raise ValueError('Standalone Python license notices are missing for build '+build_id)
    if not ripgrep.is_file():
        raise ValueError('The official Windows ripgrep executable is required')
    for name in ('COPYING', 'LICENSE-MIT', 'UNLICENSE'):
        if not (ripgrep.parent/name).is_file():
            raise ValueError('Use the official ripgrep archive with its license texts')
    app_output = output/'Ignovate-win32-x64'
    archive = output/f'Ignovate-{version}-windows-x64.zip'
    checksum = output/'SHA256SUMS-windows-desktop'
    if any(path.exists() for path in (app_output, archive, checksum)):
        raise ValueError('Use an empty output directory; existing release files are preserved')
    output.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, 'PYTHONNOUSERSITE': '1', 'PIP_DISABLE_PIP_VERSION_CHECK': '1'}
    with tempfile.TemporaryDirectory(prefix='ignovate-windows-') as folder:
        stage = Path(folder)
        runtime, tools, sources = stage/'runtime', stage/'tools', stage/'app'
        shutil.copytree(runtime_base, runtime,
                        ignore=shutil.ignore_patterns('site-packages', '__pycache__', '*.pdb'))
        tools.mkdir(); sources.mkdir()
        python = runtime/'python.exe'
        run(python, '-I', '-B', '-m', 'ensurepip', env=environment)
        run(python, '-I', '-B', '-m', 'pip', '--isolated', 'install', '--no-compile',
            '--require-hashes', '-r', ROOT/'requirements-release.lock', env=environment)
        run(python, '-I', '-B', '-m', 'pip', '--isolated', 'install', '--no-compile',
            '--no-deps', wheel.resolve(), env=environment)
        run(python, '-I', '-B', '-m', 'pip', 'check', env=environment)
        run(python, '-I', '-B', '-c', 'import nailong.gui, main, textual, mcp, langchain_deepseek', env=environment)
        # Console scripts embed temporary absolute interpreter paths. The shell
        # uses python -m, so none of those non-relocatable launchers are shipped.
        if (runtime/'Scripts').is_dir():
            shutil.rmtree(runtime/'Scripts')
        for metadata in (runtime/'Lib/site-packages').glob('*.dist-info/direct_url.json'):
            metadata.unlink()
        shutil.copy2(ripgrep, tools/'rg.exe')
        for name in ('COPYING', 'LICENSE-MIT', 'UNLICENSE'):
            shutil.copy2(ripgrep.parent/name, tools/name)
        for name in ('main.cjs', 'preload.cjs', 'runtime.cjs', 'package.json'):
            shutil.copy2(ROOT/'desktop'/name, sources/name)
        package = json.loads((sources/'package.json').read_text())
        package['version'] = version
        package.pop('devDependencies', None)
        (sources/'package.json').write_text(json.dumps(package, indent=2)+'\n', encoding='utf-8')
        electron = json.loads((ROOT/'desktop/package.json').read_text())['devDependencies']['electron']
        arguments = [sources, runtime, tools, output.resolve(), version, electron]
        if electron_zip_dir:
            arguments.append(electron_zip_dir.resolve())
        run('node', ROOT/'desktop/package-windows.mjs', *arguments)
        resources = app_output/'resources'
        shutil.copy2(ROOT/'LICENSE', resources/'IGNOVATE-LICENSE.txt')
        shutil.copytree(notices, resources/'PYTHON-THIRD-PARTY-LICENSES')
        manifest = {
            'version': version, 'platform': 'win32', 'architecture': 'x64', 'electron': electron,
            'python': subprocess.check_output([str(python), '-V'], text=True).strip(),
            'python_standalone_build': build_id,
            'ripgrep': subprocess.check_output([str(ripgrep), '--version'], text=True).splitlines()[0],
            'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'signing': 'unsigned', 'wheel_sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
            'ripgrep_sha256': hashlib.sha256(ripgrep.read_bytes()).hexdigest(),
        }
        (resources/'release-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
        (app_output/'使用说明.txt').write_text(
            '解压完整目录后双击 Ignovate.exe，选择本地工作区，再在模型设置中配置提供商。\n'
            '适用于 Windows 11 x64；内置 Python 和 ripgrep，无需安装 Python、Node.js 或 WSL。\n'
            '升级时退出旧版并解压到新目录。配置与会话保存在用户目录，删除程序目录不会删除它们。\n'
            '当前构建未使用代码签名证书，Windows 可能显示发行者未验证。\n', encoding='utf-8')
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
            for source in sorted(app_output.rglob('*')):
                if source.is_file():
                    bundle.write(source, Path('Ignovate')/source.relative_to(app_output))
    checksum.write_text(f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n',
                        encoding='utf-8', newline='\n')
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'dist/windows')
    parser.add_argument('--runtime-base', type=Path, required=True)
    parser.add_argument('--ripgrep', type=Path, default=Path(shutil.which('rg') or '/missing-ripgrep'))
    parser.add_argument('--electron-zip-dir', type=Path)
    parser.add_argument('--python-standalone-build')
    args = parser.parse_args()
    print(build_windows(args.wheel, args.output, args.runtime_base, args.ripgrep,
                        args.electron_zip_dir, args.python_standalone_build))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
