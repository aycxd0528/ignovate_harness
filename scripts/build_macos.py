"""Build a self-contained Apple Silicon Ignovate.app and drag-install DMG."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def validate_wheel(wheel: Path, version: str) -> None:
    if wheel.name!=f'ignovate_harness-{version}-py3-none-any.whl':
        raise ValueError('Wheel filename does not match release version')
    with zipfile.ZipFile(wheel) as archive:
        metadata=BytesParser().parsebytes(archive.read(f'ignovate_harness-{version}.dist-info/METADATA'))
        if metadata['Name']!='ignovate-harness' or metadata['Version']!=version:
            raise ValueError('Wheel metadata does not match release version')
        for name in archive.namelist():
            if any(part in {'.env','.idea','.nailong'} or part.startswith('.env.') for part in Path(name).parts):
                raise ValueError('Wheel contains local private files')


def standalone_build_id(runtime_base: Path, explicit: str | None) -> str:
    marker=runtime_base/'BUILD'
    recorded=marker.read_text().strip() if marker.is_file() else None
    selected=explicit or recorded
    if not selected or not re.fullmatch(r'[0-9]{8}',selected):
        raise ValueError('Specify the verified Python Standalone release with --python-standalone-build')
    if recorded and explicit and recorded!=explicit:
        raise ValueError('Python Standalone build does not match the runtime marker')
    return selected


def run(*command, cwd=None, env=None) -> None:
    subprocess.run([str(value) for value in command],cwd=cwd,env=env,check=True)


def build_macos(wheel: Path, output: Path, runtime_base: Path, ripgrep: Path, electron_zip_dir: Path | None=None, python_standalone_build: str | None=None) -> Path:
    if sys.platform!='darwin' or platform.machine()!='arm64':
        raise ValueError('Build this release on an Apple Silicon Mac')
    version=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    validate_wheel(wheel,version)
    build_id=standalone_build_id(runtime_base,python_standalone_build)
    notices=ROOT/'desktop/licenses'/('python-standalone-'+build_id)
    if not notices.is_dir():
        raise ValueError('Standalone Python license notices are missing for build '+build_id)
    if not (runtime_base/'lib/python3.11').is_dir() or not (runtime_base/'bin/python3').is_file():
        raise ValueError('A relocatable Python 3.11 distribution is required')
    if not ripgrep.is_file():
        raise ValueError('ripgrep executable is required')
    dependencies=subprocess.check_output(['otool','-L',str(ripgrep)],text=True).splitlines()[1:]
    if any(not line.strip().startswith(('/usr/lib/','/System/Library/')) for line in dependencies):
        raise ValueError('ripgrep must not depend on a local Homebrew installation')
    output.mkdir(parents=True,exist_ok=True)
    app_output=output/'Ignovate-darwin-arm64'
    dmg=output/f'Ignovate-{version}-macos-arm64.dmg'
    if app_output.exists() or dmg.exists():
        raise ValueError('Use an empty output directory; existing release files are preserved')
    environment={**os.environ,'PYTHONNOUSERSITE':'1','PIP_DISABLE_PIP_VERSION_CHECK':'1'}
    with tempfile.TemporaryDirectory(prefix='ignovate-macos-') as folder:
        stage=Path(folder);runtime=stage/'runtime';tools=stage/'tools';tools.mkdir()
        shutil.copytree(runtime_base,runtime,symlinks=True,ignore=shutil.ignore_patterns('site-packages','__pycache__'))
        # This is a fresh, private application runtime, not the managed source.
        (runtime/'lib/python3.11/EXTERNALLY-MANAGED').unlink(missing_ok=True)
        python=runtime/'bin/python3'
        run(python,'-I','-B','-m','ensurepip',env=environment)
        run(python,'-I','-B','-m','pip','--isolated','install','--no-compile','--require-hashes','-r',ROOT/'requirements-release.lock',env=environment)
        run(python,'-I','-B','-m','pip','--isolated','install','--no-compile','--no-deps',wheel.resolve(),env=environment)
        run(python,'-I','-B','-m','pip','check',env=environment)
        run(python,'-I','-B','-c','import nailong.gui, main, textual, mcp, langchain_deepseek',env=environment)
        # Desktop launch uses python -m. Pip's temporary absolute-shebang scripts
        # would not relocate, so distribute only the interpreter entry points.
        for executable in (runtime/'bin').iterdir():
            if executable.name not in {'python','python3','python3.11'}:
                executable.unlink()
        for metadata in (runtime/'lib/python3.11/site-packages').glob('*.dist-info/direct_url.json'):
            metadata.unlink()
        shutil.copy2(ripgrep,tools/'rg')
        for name in ('COPYING','LICENSE-MIT','UNLICENSE'):
            source=ripgrep.parent/name
            if not source.is_file():
                raise ValueError('Use the official ripgrep release archive with its license texts')
            shutil.copy2(source,tools/name)
        app_sources=stage/'app';app_sources.mkdir()
        for name in ('main.cjs','preload.cjs','runtime.cjs','workspace.cjs','package.json'):
            shutil.copy2(ROOT/'desktop'/name,app_sources/name)
        package=json.loads((app_sources/'package.json').read_text());package['version']=version;package.pop('devDependencies',None)
        (app_sources/'package.json').write_text(json.dumps(package,indent=2)+'\n')
        electron=json.loads((ROOT/'desktop/package.json').read_text())['devDependencies']['electron']
        arguments=[app_sources,runtime,tools,output.resolve(),version,electron]
        if electron_zip_dir:arguments.append(electron_zip_dir.resolve())
        run('node',ROOT/'desktop/package-macos.mjs',*arguments)
        app=app_output/'Ignovate.app';resources=app/'Contents/Resources'
        # Node fs.cp rewrites relative symlinks to staging paths. Preserve the
        # Python distribution's relative links when copying the private runtime.
        shutil.copytree(runtime,resources/'runtime',symlinks=True)
        for name in ('LICENSE','LICENSES.chromium.html'):
            shutil.copy2(app_output/name,resources/('ELECTRON-'+name))
        shutil.copy2(ROOT/'LICENSE',resources/'IGNOVATE-LICENSE.txt')
        shutil.copytree(notices,resources/'PYTHON-THIRD-PARTY-LICENSES')
        manifest={'version':version,'architecture':'arm64','electron':electron,'python':subprocess.check_output([str(python),'-V'],text=True).strip(),
                  'ripgrep':subprocess.check_output([str(ripgrep),'--version'],text=True).splitlines()[0],
                  'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                  'signing':'ad-hoc','notarized':False,'wheel_sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),
                  'ripgrep_sha256':hashlib.sha256(ripgrep.read_bytes()).hexdigest(),'python_standalone_build':build_id}
        (resources/'release-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        binaries=[p for p in (resources/'runtime').rglob('*') if p.is_file() and not p.is_symlink() and (p.suffix in {'.so','.dylib'} or p.name=='python3.11')]
        for binary in [*binaries,resources/'tools/rg']:
            run('codesign','--force','--sign','-',binary)
        run('codesign','--force','--deep','--sign','-','--entitlements',ROOT/'desktop/entitlements.mac.plist',app)
        run('codesign','--verify','--deep','--strict',app)
        image=stage/'image';image.mkdir();shutil.copytree(app,image/'Ignovate.app',symlinks=True)
        (image/'Applications').symlink_to('/Applications',target_is_directory=True)
        (image/'安装说明.txt').write_text('将 Ignovate.app 拖入 Applications，再从应用程序打开。\n首次启动请选择工作区；模型设置中配置提供商。\n此包适用于 Apple Silicon，包含 Python 与 ripgrep，无需安装 Python。\n此构建采用本地 ad hoc 签名，尚未取得 Developer ID 签名或 Apple 公证。\n',encoding='utf-8')
        run('hdiutil','create','-volname',f'Ignovate {version}','-srcfolder',image,'-format','UDZO',dmg.resolve())
        run('hdiutil','verify',dmg.resolve())
    (output/'SHA256SUMS-macos').write_text(f'{hashlib.sha256(dmg.read_bytes()).hexdigest()}  {dmg.name}\n')
    return dmg


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'dist/macos')
    parser.add_argument('--runtime-base',type=Path,default=Path(sys.base_prefix))
    parser.add_argument('--ripgrep',type=Path,default=Path(shutil.which('rg') or '/missing-ripgrep'))
    parser.add_argument('--electron-zip-dir',type=Path,help='Directory containing an official Electron ZIP (optional offline cache)')
    parser.add_argument('--python-standalone-build',help='Verified official release ID for archives without a uv BUILD marker')
    args=parser.parse_args()
    print(build_macos(args.wheel,args.output,args.runtime_base,args.ripgrep,args.electron_zip_dir,args.python_standalone_build))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
