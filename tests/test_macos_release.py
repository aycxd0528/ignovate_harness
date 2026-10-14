import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/build_macos.py'
spec=importlib.util.spec_from_file_location('macos_build',SCRIPT)
build=importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

class MacReleaseManifestTests(unittest.TestCase):
    def test_matching_wheel_is_required_before_packaging(self):
        with tempfile.TemporaryDirectory() as folder:
            wheel=Path(folder)/'ignovate_harness-1.1.0-py3-none-any.whl'
            with zipfile.ZipFile(wheel,'w') as archive:
                archive.writestr('ignovate_harness-1.1.0.dist-info/METADATA','Name: ignovate-harness\nVersion: 1.1.0\n')
            build.validate_wheel(wheel,'1.1.0')
            with self.assertRaises(ValueError):build.validate_wheel(wheel,'1.0.3')

    def test_private_files_are_rejected_inside_the_application_wheel(self):
        with tempfile.TemporaryDirectory() as folder:
            wheel=Path(folder)/'ignovate_harness-1.1.0-py3-none-any.whl'
            with zipfile.ZipFile(wheel,'w') as archive:
                archive.writestr('ignovate_harness-1.1.0.dist-info/METADATA','Name: ignovate-harness\nVersion: 1.1.0\n')
                archive.writestr('.nailong/gui-settings.json','{}')
            with self.assertRaises(ValueError):build.validate_wheel(wheel,'1.1.0')

    def test_official_archive_accepts_explicit_build_id_without_uv_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(build.standalone_build_id(Path(folder), '20260510'), '20260510')

    def test_local_marker_must_match_explicit_build_id(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'BUILD').write_text('20260510\n')
            self.assertEqual(build.standalone_build_id(root, None), '20260510')
            with self.assertRaises(ValueError):build.standalone_build_id(root, '20260101')
            with self.assertRaises(ValueError):build.standalone_build_id(root, '../untrusted')

    def test_archive_requires_its_verified_build_id(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):build.standalone_build_id(Path(folder), None)
