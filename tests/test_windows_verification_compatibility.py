"""Portable path contracts plus native Windows verification ownership checks."""
import os
from pathlib import Path, PureWindowsPath
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class VerificationCompatibilityTests(unittest.TestCase):
    def test_long_windows_shell_dispatch_supplies_verified_alias(self):
        import sys
        import nailong.core.process_io as process_io
        root=PureWindowsPath('C:/project').joinpath(*(['long-component']*25))
        process=Mock(stdin=None,stdout=None,stderr=None)
        job=Mock()
        modules={'nailong.core._windows_process':SimpleNamespace(WindowsJob=lambda:job),
                 'nailong.core._win32_files':SimpleNamespace(short_directory=lambda path:'C:\\short')}
        with patch.dict(sys.modules,modules), \
                patch.object(process_io,'os',SimpleNamespace(name='nt',fspath=os.fspath)), \
                patch('nailong.core.safe_files._path',return_value=root), \
                patch.object(process_io,'shell_command',return_value=(['shell'],False)) as build, \
                patch.object(process_io.subprocess,'Popen',return_value=process) as popen:
            owner=process_io.OwnedProcess('user-command',cwd=root,shell=True)
            try:
                build.assert_called_once_with('user-command',cwd='C:\\short')
                self.assertEqual(popen.call_args.kwargs['cwd'],'C:\\short')
                self.assertFalse(popen.call_args.kwargs['shell'])
            finally:owner.close()

    def test_windows_shell_keeps_verified_short_location_before_user_command(self):
        import base64
        import nailong.core.process_io as process_io
        with patch.object(process_io,'os',SimpleNamespace(name='nt',environ={},fspath=os.fspath)):
            command,shell=process_io.shell_command('user-command',cwd="C:\\SHORT'S\\PROJECT")
        self.assertFalse(shell)
        script=base64.b64decode(command[-1]).decode('utf-16-le')
        self.assertIn("Set-Location -LiteralPath 'C:\\SHORT''S\\PROJECT' -ErrorAction Stop;",script)
        self.assertLess(script.index('Set-Location'),script.index('user-command'))

    def test_short_alias_checks_length_and_native_directory_identity(self):
        import ast
        import ctypes
        from contextlib import contextmanager
        source=Path(__file__).resolve().parents[1]/'nailong/core/_win32_files.py'
        tree=ast.parse(source.read_text())
        functions=[node for node in tree.body if isinstance(node,ast.FunctionDef)
                   and node.name in {'extended','short_directory'}]
        state={'short':'\\\\?\\C:\\SHORT','mismatch':False}
        pins=[]
        @contextmanager
        def pin(path):
            pins.append(('enter',str(path)))
            try:yield str(path)
            finally:pins.append(('exit',str(path)))
        original=PureWindowsPath('C:/project').joinpath(*(['long-component']*25))
        def get_short(path,buffer,size):
            if buffer is None:return len(state['short'])+1
            buffer.value=state['short']
            return len(state['short'])
        def info(handle):
            return SimpleNamespace(volume=1,index_high=0,
                index_low=2 if state['mismatch'] and handle!=str(original) else 1)
        namespace={'os':os,'ctypes':ctypes,'GetShortPathName':get_short,
                   'pin_directory':pin,'information':info,'_error':lambda path:OSError('native error')}
        exec(compile(ast.Module(body=functions,type_ignores=[]),str(source),'exec'),namespace)
        self.assertEqual(namespace['short_directory'](original),'C:\\SHORT')
        self.assertEqual(len([x for x in pins if x[0]=='enter']),2)
        self.assertEqual(len([x for x in pins if x[0]=='exit']),2)
        state['mismatch']=True
        with self.assertRaises(ValueError):namespace['short_directory'](original)
        state['short']=str(original)
        with self.assertRaises(ValueError):namespace['short_directory'](original)

    def test_long_cwd_dispatch_counts_absolute_utf16_units(self):
        import sys
        import nailong.core.process_io as process_io
        ascii_path=PureWindowsPath('C:/project').joinpath(*(['segment'*4]*14))
        emoji_path=PureWindowsPath('C:/project').joinpath(*(['😀'*50]*3))
        self.assertLess(len(str(emoji_path)), 260)
        self.assertGreaterEqual(len(str(emoji_path).encode('utf-16-le'))//2, 260)
        for raw,absolute in ((ascii_path,ascii_path),(emoji_path,emoji_path),('child',ascii_path)):
            with self.subTest(raw=raw):
                process=Mock(stdin=None,stdout=None,stderr=None)
                job=Mock()
                modules={'nailong.core._windows_process':SimpleNamespace(WindowsJob=lambda:job),
                         'nailong.core._win32_files':SimpleNamespace(short_directory=lambda path:'C:\\short')}
                with patch.dict(sys.modules,modules), \
                        patch.object(process_io,'os',SimpleNamespace(name='nt',fspath=os.fspath)), \
                        patch('nailong.core.safe_files._path',return_value=absolute), \
                        patch.object(process_io.subprocess,'Popen',return_value=process) as popen:
                    owner=process_io.OwnedProcess(['child'],cwd=raw)
                    try:self.assertEqual(popen.call_args.kwargs['cwd'],'C:\\short')
                    finally:owner.close()

    def test_empty_git_identity_variables_are_explicit_and_refused(self):
        import nailong.core.verification as verification
        with tempfile.TemporaryDirectory() as directory:
            for key in ('GIT_DIR','GIT_COMMON_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_OBJECT_DIRECTORY'):
                with self.subTest(key=key), patch.dict(os.environ,{key:''},clear=True), \
                        patch('shutil.which',return_value=None), \
                        patch.object(verification.subprocess,'run',side_effect=FileNotFoundError('git')):
                    with self.assertRaises(ValueError):
                        verification._git(Path(directory),'rev-parse','HEAD')

    def test_external_git_objects_prevent_non_repository_shortcut(self):
        import nailong.core.verification as verification
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'HEAD').write_text('ref: refs/heads/main')
            (root/'refs').mkdir()
            for value in ('',str(root/'external-objects')):
                with self.subTest(value=value), patch.dict(os.environ,{'GIT_OBJECT_DIRECTORY':value},clear=True):
                    self.assertFalse(verification._non_repository(root))

    def test_windows_manifest_accepts_canonical_slash_and_native_paths(self):
        import nailong.core.verification_artifacts as artifacts
        with patch.object(artifacts, 'Path', PureWindowsPath):
            for raw in ('bundle/product', 'bundle\\product'):
                with self.subTest(raw=raw):
                    self.assertEqual(artifacts._path(raw), PureWindowsPath('bundle/product'))
            for raw in ('C:product', '\\product', 'C:/product', '../product',
                        'bundle//product', 'bundle/./product'):
                with self.subTest(raw=raw), self.assertRaises(ValueError):
                    artifacts._path(raw)

    def test_missing_git_allows_only_a_proven_non_repository(self):
        import nailong.core.verification as verification
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'project'
            root.mkdir()
            with patch.object(verification.subprocess, 'run', side_effect=FileNotFoundError('git')), \
                    patch('shutil.which', return_value=None), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(verification._git(root, 'rev-parse', 'HEAD'), b'')
                for kind in ('file', 'directory'):
                    marker=root/'.git'
                    if kind=='file':marker.write_text('gitdir: missing')
                    else:marker.mkdir()
                    with self.subTest(kind=kind), self.assertRaises(ValueError):
                        verification._git(root, 'rev-parse', 'HEAD')
                    if kind=='file':marker.unlink()
                    else:marker.rmdir()
                with patch.dict(os.environ, {'GIT_DIR':'external'}), self.assertRaises(ValueError):
                    verification._git(root, 'rev-parse', 'HEAD')
                (root.parent/'.git').mkdir()
                with self.assertRaises(ValueError):verification._git(root, 'rev-parse', 'HEAD')
                (root.parent/'.git').rmdir()
                for name in ('objects','refs'):(root/name).mkdir()
                (root/'HEAD').write_text('ref: refs/heads/main')
                with self.assertRaises(ValueError):verification._git(root, 'rev-parse', 'HEAD')

    def test_windows_git_uses_short_process_cwd_and_explicit_project(self):
        import sys
        import nailong.core.verification as verification
        root=PureWindowsPath('C:/project')/('deep'*80)
        with patch.object(verification, 'os', SimpleNamespace(name='nt', environ={})), \
                patch.object(verification,'_non_repository',return_value=False), \
                patch('nailong.core.safe_files._path',return_value=root), \
                patch.dict(sys.modules,{'nailong.core._win32_files':SimpleNamespace(short_directory=lambda path:'C:\\short')}), \
                patch.object(verification.subprocess, 'run', return_value=
                             subprocess.CompletedProcess([], 0, b'identity', b'')) as run:
            self.assertEqual(verification._git(root, 'rev-parse', 'HEAD'), b'identity')
        self.assertEqual(run.call_args.kwargs['cwd'], 'C:\\short')
        command=run.call_args.args[0]
        self.assertNotIn('-C',command)


@unittest.skipUnless(os.name=='nt', 'requires actual Win32 directory sharing')
class NativeWindowsDirectoryGuardTests(unittest.TestCase):
    def test_shell_child_runs_in_deep_unicode_directory(self):
        import asyncio
        from platform_fixtures import python_command
        from nailong.core.processes import execute_process
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).joinpath(*(['工程目录'*3]*20))
            root.mkdir(parents=True)
            command=python_command("from pathlib import Path;Path('shell-proof').write_text('native shell cwd')")
            result=asyncio.run(execute_process(command,root,timeout=20))
            self.assertTrue(result['ok'],result)
            self.assertEqual((root/'shell-proof').read_text(),'native shell cwd')

    def test_owned_process_runs_in_a_native_long_directory(self):
        import sys
        from nailong.core.process_io import OwnedProcess
        with tempfile.TemporaryDirectory() as temporary:
            for parts in (['long-directory-component']*15,['😀'*45]*3):
                with self.subTest(parts=parts):
                    root=Path(temporary).joinpath(*parts)
                    root.mkdir(parents=True)
                    self.assertGreaterEqual(len(str(root).encode('utf-16-le'))//2, 260)
                    if parts[0].startswith('😀'):self.assertLess(len(str(root)),260)
                    owner=OwnedProcess([sys.executable, '-c',
                        "from pathlib import Path;Path('proof.txt').write_text('native long cwd')"],
                        cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    try:
                        output,error=owner.process.communicate(timeout=20)
                        self.assertEqual(owner.process.returncode, 0, (output,error))
                    finally:
                        owner.close()
                    self.assertEqual((root/'proof.txt').read_text(), 'native long cwd')

    def test_guard_allows_child_outputs_but_prevents_parent_replacement(self):
        from nailong.core.verification_directories import DirectoryGuard
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'project'
            root.mkdir()
            guard=DirectoryGuard()
            try:
                guard.start(root, {Path('.')})
                self.assertIn(Path('.'), guard.protected)
                (root/'bundle').mkdir()
                (root/'bundle/product').write_text('generated')
                guard.check()
                with self.assertRaises(OSError):root.rename(root.with_name('moved'))
                guard.check()
            finally:
                guard.close()
            moved=root.with_name('moved')
            root.rename(moved)
            self.assertEqual((moved/'bundle/product').read_text(), 'generated')

    def test_guard_releases_native_pins_when_a_later_pin_fails(self):
        import nailong.core.verification_directories as directories
        from contextlib import contextmanager
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'project'
            (root/'nested').mkdir(parents=True)
            native=directories.pinned_directory
            @contextmanager
            def fail_second(path):
                if Path(path).name=='nested':raise OSError('injected second pin failure')
                with native(path) as handle:yield handle
            guard=directories.DirectoryGuard()
            with patch.object(directories, 'pinned_directory', fail_second), self.assertRaises(OSError):
                guard.start(root, {Path('.'), Path('nested')})
            self.assertEqual(guard.protected, set())
            root.rename(root.with_name('moved'))
