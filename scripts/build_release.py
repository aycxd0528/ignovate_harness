"""Create Python-free Unix/Windows bootstrap bundles from a validated wheel."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
from pathlib import Path
import shutil
import tarfile
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def checksums(directory: Path, files: list[Path]) -> None:
    (directory/'SHA256SUMS').write_text(''.join(
        f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n'
        for path in sorted(files)), encoding='utf-8', newline='\n')


def build_release(wheel: Path, output: Path) -> list[Path]:
    version = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    expected_name = f'ignovate_harness-{version}-py3-none-any.whl'
    if wheel.name != expected_name:
        raise ValueError(f'Expected {expected_name}; got {wheel.name}')
    with zipfile.ZipFile(wheel) as archive:
        metadata = BytesParser().parsebytes(archive.read(
            f'ignovate_harness-{version}.dist-info/METADATA'))
        if metadata['Name'] != 'ignovate-harness' or metadata['Version'] != version:
            raise ValueError('Wheel metadata does not match release version')
        if any(name.startswith(('.env', '.idea/', 'artifacts/')) or '/.env' in name
               for name in archive.namelist()):
            raise ValueError('Wheel contains local private files')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ignovate-release-') as directory:
        bundle = Path(directory)/f'ignovate-{version}'
        bundle.mkdir()
        for source in [wheel, ROOT/'requirements-release.lock', ROOT/'LICENSE',
                       ROOT/'docs/INSTALL.md', *sorted((ROOT/'scripts/release').glob('*'))]:
            if source.is_file():
                shutil.copyfile(source, bundle/source.name)
        (bundle/'VERSION').write_text(version+'\n', encoding='utf-8')
        for name in ('install.sh', 'launch.sh'):
            (bundle/name).chmod(0o755)
        checksums(bundle, list(bundle.iterdir()))
        unix = output/f'ignovate-{version}-unix.tar.gz'
        with tarfile.open(unix, 'w:gz') as archive:
            def portable_mode(info):
                info.mode = 0o755 if info.isdir() or info.name.endswith(('/install.sh', '/launch.sh')) else 0o644
                return info
            archive.add(bundle, arcname=bundle.name, filter=portable_mode)
        windows = output/f'ignovate-{version}-windows.zip'
        with zipfile.ZipFile(windows, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(bundle.iterdir()):
                archive.write(path, f'{bundle.name}/{path.name}')
    shipped_wheel = output/wheel.name
    if wheel.resolve() != shipped_wheel.resolve():
        shutil.copyfile(wheel, shipped_wheel)
    files = [unix, windows, shipped_wheel]
    checksums(output, files)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('dist/release'))
    args = parser.parse_args()
    for path in build_release(args.wheel, args.output):
        print(path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
