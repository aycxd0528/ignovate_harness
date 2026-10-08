"""Real-process contracts shared by POSIX and native Windows runners."""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nailong.core import process_io
from nailong.core.hooks import HookRunner
from nailong.core.plan import edit_plan_with_editor, edit_plan_with_editor_async
from nailong.core.processes import ProcessCancelled, execute_process


def shell_python(code: str) -> str:
    if os.name == 'nt':
        # PowerShell 5.1's legacy native quoting strips embedded double quotes.
        # Transport arbitrary Python source as hex inside an unambiguous argument.
        code = "exec(bytes.fromhex('" + code.encode('utf-8').hex() + "').decode('utf-8'))"
        return "& '" + sys.executable.replace("'", "''") + "' -c '" + code.replace("'", "''") + "'"
    return shlex.join([sys.executable, '-c', code])



class PortableCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='进程 tests ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_shell_capture_decodes_utf8_and_keeps_head_and_tail_with_a_bound(self):
        command = shell_python("import sys;sys.stdout.buffer.write(('开始\\n'+'x'*40000+'\\n最后错误\\n').encode('utf-8'));sys.exit(3)")
        output, truncated, timed_out, exit_code = process_io.capture_command_output(
            command, cwd=self.root, timeout=3, max_output_chars=1200)
        self.assertIn('开始', output)
        self.assertIn('最后错误', output)
        self.assertLessEqual(len(output), 1200)
        self.assertTrue(truncated)
        self.assertFalse(timed_out)
        self.assertEqual(exit_code, 3)

    def test_raw_capture_bounds_bytes_and_preserves_stdin_and_arguments(self):
        script = "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read()+('|'.join(sys.argv[1:])).encode('utf-8'))"
        output, exit_code, truncated = process_io.capture_bytes(
            [sys.executable, '-c', script, '', 'has space', 'a"b', '尾\\'],
            cwd=self.root, input_bytes='输入:'.encode(), timeout=3)
        self.assertEqual(output.decode(), '输入:|has space|a"b|尾\\')
        self.assertEqual(exit_code, 0)
        self.assertFalse(truncated)
        output, _, truncated = process_io.capture_bytes(
            [sys.executable, '-c', "import sys;sys.stdout.buffer.write(b'x'*1000000)"],
            cwd=self.root, max_output_bytes=1000, timeout=3)
        self.assertEqual(output, b'x'*1000)
        self.assertTrue(truncated)

    def test_short_output_overflow_captures_cleanup_during_process_exit(self):
        # Darwin can report EPERM for a group while its last process exits,
        # before waitpid can reap it. Repeat real processes to exercise that race.
        repetitions = 250 if sys.platform == 'darwin' else 32
        for _ in range(repetitions):
            output, _, truncated = process_io.capture_bytes(
                [sys.executable, '-c', "import sys;sys.stdout.buffer.write(b'x'*1000000)"],
                cwd=self.root, max_output_bytes=1000, timeout=3)
            self.assertEqual(output, b'x'*1000)
            self.assertTrue(truncated)

    def test_timeout_owns_child_until_its_late_write_is_impossible(self):
        marker = self.root / 'late.txt'
        timeout = 2 if os.name == 'nt' else .2
        child = f"import time;from pathlib import Path;time.sleep({timeout+.6});Path({str(marker)!r}).write_text('leak')"
        code = f"import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',{child!r}]);print('started',flush=True);time.sleep(30)"
        output, _, timed_out, _ = process_io.capture_command_output(shell_python(code), cwd=self.root, timeout=timeout)
        self.assertTrue(timed_out)
        self.assertIn('started', output)
        time.sleep(timeout+.8)
        self.assertFalse(marker.exists())

    def test_raw_capture_timeout_raises_after_descendants_are_stopped(self):
        marker = self.root / 'raw-late.txt'
        timeout = 1 if os.name == 'nt' else .2
        child = f"import time;from pathlib import Path;time.sleep({timeout+.6});Path({str(marker)!r}).write_text('leak')"
        code = f"import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',{child!r}]);print('spawned',flush=True);time.sleep(30)"
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            process_io.capture_bytes([sys.executable, '-c', code], cwd=self.root, timeout=timeout)
        self.assertIn(b'spawned', caught.exception.output)
        time.sleep(timeout+.8)
        self.assertFalse(marker.exists())


class PortableAsyncProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='异步 process ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    async def wait_started(self, path):
        async with asyncio.timeout(3):
            while not path.exists():
                await asyncio.sleep(.01)

    def spawning_command(self, started, marker):
        child = f"import time;from pathlib import Path;time.sleep(.8);Path({str(marker)!r}).write_text('leak')"
        return shell_python(f"import subprocess,sys,time;from pathlib import Path;subprocess.Popen([sys.executable,'-c',{child!r}]);Path({str(started)!r}).write_text('started');print('ready',flush=True);time.sleep(30)")

    async def test_async_cancellation_cleans_descendant_before_returning(self):
        started, marker = self.root/'started', self.root/'async-late'
        task = asyncio.create_task(execute_process(self.spawning_command(started, marker), self.root))
        await self.wait_started(started)
        task.cancel()
        with self.assertRaises(ProcessCancelled) as caught:
            await task
        self.assertTrue(caught.exception.result['cancelled'])
        self.assertIn('ready', caught.exception.result['output'])
        await asyncio.sleep(1)
        self.assertFalse(marker.exists())

    async def test_repeated_async_cancellation_still_joins_tree_cleanup(self):
        started, marker = self.root/'repeated', self.root/'repeated-late'
        task = asyncio.create_task(execute_process(self.spawning_command(started, marker), self.root))
        await self.wait_started(started)
        task.cancel()
        while not task.done():
            await asyncio.sleep(.001)
            if not task.done():
                task.cancel()
        with self.assertRaises(ProcessCancelled) as caught:
            await task
        self.assertTrue(caught.exception.result['cancelled'])
        await asyncio.sleep(1)
        self.assertFalse(marker.exists())

    async def test_http_observation_cleans_server_and_descendant(self):
        marker = self.root/'server-late'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        child = f"import time;from pathlib import Path;time.sleep(1);Path({str(marker)!r}).write_text('leak')"
        command = shell_python(f"import subprocess,sys;from http.server import HTTPServer,SimpleHTTPRequestHandler;subprocess.Popen([sys.executable,'-c',{child!r}]);HTTPServer(('127.0.0.1',{port}),SimpleHTTPRequestHandler).serve_forever()")
        result = await execute_process(command, self.root, http_url=f'http://127.0.0.1:{port}/', timeout=5)
        self.assertTrue(result['ok'])
        self.assertTrue(result['observed'])
        await asyncio.sleep(1.2)
        self.assertFalse(marker.exists())
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(('127.0.0.1', port)), 0)

    async def test_observation_stops_parent_and_descendant(self):
        started, marker = self.root/'observed', self.root/'observed-late'
        result = await execute_process(self.spawning_command(started, marker), self.root,
                                       stdout_contains='ready', timeout=3)
        self.assertTrue(result['ok'])
        self.assertTrue(result['observed'])
        await asyncio.sleep(1)
        self.assertFalse(marker.exists())

    async def test_stdout_observation_detects_flushed_marker_without_newline(self):
        command = shell_python("import sys,time;sys.stdout.buffer.write('开头READY'.encode('utf-8'));sys.stdout.buffer.flush();time.sleep(30)")
        result = await execute_process(command, self.root, stdout_contains='READY', timeout=5)
        self.assertTrue(result['ok'])
        self.assertTrue(result['observed'])
        self.assertIn('开头READY', result['output'])

    async def test_hook_cancellation_stops_worker_and_descendant(self):
        started, marker = self.root/'hook-started', self.root/'hook-late'
        settings = self.root/'.nailong'; settings.mkdir()
        command = self.spawning_command(started, marker)
        (settings/'settings.json').write_text(json.dumps({'hooks': {'PreToolUse': [
            {'hooks': [{'type': 'command', 'command': command}]}]}}), encoding='utf-8', newline='\n')
        runner = HookRunner(self.root)
        task = asyncio.create_task(runner.run_event('PreToolUse', confirm=lambda _: True))
        await self.wait_started(started)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(1)
        self.assertFalse(marker.exists())

    async def test_hook_bounded_separate_utf8_streams_and_exit_two_blocking(self):
        settings = self.root/'.nailong'; settings.mkdir()
        code = "import sys;sys.stdout.buffer.write(('首'+'x'*30000).encode());sys.stderr.buffer.write(('错误'+'z'*30000).encode());sys.exit(2)"
        (settings/'settings.json').write_text(json.dumps({'hooks': {'PreToolUse': [
            {'hooks': [{'type': 'command', 'command': shell_python(code)}]}]}}), encoding='utf-8', newline='\n')
        result = await HookRunner(self.root, max_output_chars=512).run_event('PreToolUse', confirm=lambda _: True)
        self.assertTrue(result.blocked)
        self.assertIn('首', result.outputs[0].stdout)
        self.assertIn('错误', result.outputs[0].stderr)
        self.assertLessEqual(len(result.outputs[0].stdout), 512)
        self.assertLessEqual(len(result.outputs[0].stderr), 512)

    async def test_hook_retains_universal_newline_text_behavior(self):
        code = "import sys,time;sys.stdout.buffer.write(b'first\\r');sys.stdout.buffer.flush();time.sleep(.03);sys.stdout.buffer.write(b'\\nsecond\\rthird\\n');sys.stderr.buffer.write(b'error\\r\\n')"
        result = HookRunner(self.root)._execute(shell_python(code), process_io.command_environment())
        self.assertEqual(result.stdout, 'first\nsecond\nthird\n')
        if os.name == 'posix':
            self.assertEqual(result.stderr, 'error\n')
        else:
            self.assertIn('error', result.stderr)
            self.assertNotIn('\r', result.stderr)

    async def test_editor_cancel_removes_private_copy_and_kills_descendant(self):
        info, marker = self.root/'editor-info.json', self.root/'editor-late'
        child = f"import time;from pathlib import Path;time.sleep(.8);Path({str(marker)!r}).write_text('leak')"
        script = self.root/'editor helper.py'
        script.write_text(f"import json,os,subprocess,sys,time\nfrom pathlib import Path\nsubprocess.Popen([sys.executable,'-c',{child!r}])\nPath({str(info)!r}).write_text(json.dumps({{'path':sys.argv[-1],'key':os.getenv('DEEPSEEK_API_KEY')}}))\ntime.sleep(30)\n", encoding='utf-8', newline='\n')
        selected = subprocess.list2cmdline([sys.executable, str(script)]) if os.name == 'nt' else shlex.join([sys.executable, str(script)])
        with patch.dict(os.environ, {'VISUAL': selected, 'DEEPSEEK_API_KEY': 'private-key'}):
            task = asyncio.create_task(edit_plan_with_editor_async('original'))
            await self.wait_started(info)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        data = json.loads(info.read_text())
        self.assertIsNone(data['key'])
        self.assertFalse(Path(data['path']).exists())
        await asyncio.sleep(1)
        self.assertFalse(marker.exists())

    async def test_sync_editor_does_not_receive_model_credential(self):
        script = self.root/'secret editor.py'
        script.write_text("import os,sys\nfrom pathlib import Path\nPath(sys.argv[-1]).write_text(os.getenv('DEEPSEEK_API_KEY','missing'),encoding='utf-8')\n", encoding='utf-8', newline='\n')
        selected = subprocess.list2cmdline([sys.executable, str(script)]) if os.name == 'nt' else shlex.join([sys.executable, str(script)])
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'private-key'}):
            self.assertEqual(edit_plan_with_editor('before', editor=selected), 'missing')

    async def test_editor_exec_preserves_quoted_paths_and_reads_updated_copy(self):
        script = self.root/'write editor.py'
        script.write_text("import sys\nfrom pathlib import Path\nPath(sys.argv[-1]).write_text('修改后',encoding='utf-8')\n", encoding='utf-8', newline='\n')
        selected = subprocess.list2cmdline([sys.executable, str(script)]) if os.name == 'nt' else shlex.join([sys.executable, str(script)])
        self.assertEqual(edit_plan_with_editor('before', editor=selected), '修改后')
        with patch.dict(os.environ, {'VISUAL': selected}):
            self.assertEqual(await edit_plan_with_editor_async('before'), '修改后')


if __name__ == '__main__':
    unittest.main()
