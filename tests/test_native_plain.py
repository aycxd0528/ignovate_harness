"""Real plain-UI input and signal contracts, including Windows' Proactor loop."""
from __future__ import annotations

import asyncio
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ui.plain import PlainPromptSession


class NativePlainInputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        read_fd, self.write_fd = os.pipe()
        self.input = os.fdopen(read_fd, 'r', encoding='utf-8')
        self.output = io.StringIO()
        self.session = PlainPromptSession(self.input, self.output)
        self.addCleanup(self.input.close)
        self.addCleanup(self.close_writer)

    def close_writer(self):
        if self.write_fd is not None:
            os.close(self.write_fd)
            self.write_fd = None

    async def test_pipe_reads_split_utf8_crlf_buffered_lines_and_default(self):
        task = asyncio.create_task(self.session.prompt_async('> '))
        await asyncio.sleep(.03)
        encoded = '第一行\r\n第二行\n\n'.encode()
        os.write(self.write_fd, encoded[:2])
        await asyncio.sleep(.03)
        os.write(self.write_fd, encoded[2:])
        self.assertEqual(await asyncio.wait_for(task, 2), '第一行')
        self.assertEqual(await self.session.prompt_async('> '), '第二行')
        self.assertEqual(await self.session.prompt_async('> ', default='draft'), 'draft')

    async def test_pipe_partial_line_before_eof_is_delivered_once(self):
        os.write(self.write_fd, '末尾'.encode())
        self.close_writer()
        self.assertEqual(await asyncio.wait_for(self.session.prompt_async('> '), 2), '末尾')
        with self.assertRaises(EOFError):
            await self.session.prompt_async('> ')

    async def test_cancelled_reader_cannot_consume_the_next_prompt_input(self):
        cancelled = asyncio.create_task(self.session.prompt_async('> '))
        await asyncio.sleep(.03)
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        next_prompt = asyncio.create_task(self.session.prompt_async('> '))
        await asyncio.sleep(.03)
        os.write(self.write_fd, b'future input\n')
        self.assertEqual(await asyncio.wait_for(next_prompt, 2), 'future input')

    async def test_closed_session_wakes_reader_and_leaves_stream_for_its_next_owner(self):
        task = asyncio.create_task(self.session.prompt_async('> '))
        await asyncio.sleep(.03)
        self.session.close()
        with self.assertRaises(EOFError):
            await asyncio.wait_for(task, 2)
        next_owner = PlainPromptSession(self.input, self.output)
        next_prompt = asyncio.create_task(next_owner.prompt_async('> '))
        await asyncio.sleep(.03)
        os.write(self.write_fd, b'next owner\n')
        self.assertEqual(await asyncio.wait_for(next_prompt, 2), 'next owner')

    async def test_inline_restores_sigint_even_when_runner_cleanup_fails(self):
        from unittest.mock import patch
        from config import Settings
        from ui.app import run_inline
        from ui.console import Console
        from rich.console import Console as RichConsole
        class Prompt:
            async def prompt_async(self, message, **options):
                return '/exit'
        previous = signal.getsignal(signal.SIGINT)
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with patch.dict(os.environ, {'NAILONG_DATA_DIR': str(root/'private')}), \
                        patch('ui.app.SessionRunner.close', side_effect=RuntimeError('cleanup failed')):
                    with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
                        await run_inline(Settings('fake', 'https://api.invalid', 'deepseek-flash', root),
                            plain=True, prompt_session=Prompt(),
                            console=Console(console=RichConsole(file=io.StringIO(), no_color=True)))
            self.assertEqual(signal.getsignal(signal.SIGINT), previous)
        finally:
            if os.name != 'nt':
                asyncio.get_running_loop().remove_signal_handler(signal.SIGINT)
            signal.signal(signal.SIGINT, previous)


class NativePlainProcessTests(unittest.TestCase):
    def run_python(self, source, *, timeout=15, **options):
        return subprocess.run([sys.executable, '-c', source], capture_output=True,
                              text=True, encoding='utf-8', timeout=timeout, **options)

    def test_interrupt_callback_is_async_and_previous_sigint_handler_is_restored(self):
        result = self.run_python("""
import asyncio,json,signal
from ui.plain import install_plain_interrupt
async def main():
    previous=signal.getsignal(signal.SIGINT)
    seen=[]
    cleanup=install_plain_interrupt(lambda:seen.append('interrupted'))
    try:
        signal.raise_signal(signal.SIGINT)
        await asyncio.sleep(.05)
    finally:
        cleanup()
    print(json.dumps({'seen':seen,'restored':signal.getsignal(signal.SIGINT)==previous}))
asyncio.run(main())
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'seen': ['interrupted'], 'restored': True})

    def test_real_plain_cli_accepts_pipe_commands_and_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root/'project'; project.mkdir()
            result = subprocess.run([sys.executable, 'main.py', '--plain', '--project', str(project)],
                input='/help\n/exit\n', capture_output=True, text=True, encoding='utf-8', timeout=20,
                env={**os.environ, 'DEEPSEEK_API_KEY': 'plain-fixture-key',
                     'DEEPSEEK_BASE_URL': 'https://api.invalid', 'DEEPSEEK_MODEL': 'deepseek-flash',
                     'NAILONG_DATA_DIR': str(root/'private'), 'NO_COLOR': '1', 'PYTHONUTF8': '1'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('输入与快捷键', result.stdout)
        self.assertIn('再见', result.stdout)

    @unittest.skipUnless(os.name == 'nt', 'Requires actual Windows console input records.')
    def test_native_console_unicode_backspace_enter_and_ctrl_c(self):
        # Allocate an isolated console inside the subprocess so synthetic events
        # cannot reach the developer's terminal or any unrelated process.
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        result = self.run_python(r"""
import asyncio,ctypes,io,json,signal
from ctypes import wintypes
from ui.plain import PlainPromptSession,install_plain_interrupt
k=ctypes.WinDLL('kernel32',use_last_error=True)
class Key(ctypes.Structure):
    _fields_=[('down',wintypes.BOOL),('repeat',wintypes.WORD),('key',wintypes.WORD),('scan',wintypes.WORD),('char',wintypes.WCHAR),('state',wintypes.DWORD)]
class Event(ctypes.Union):
    _fields_=[('key',Key),('padding',ctypes.c_byte*16)]
class Record(ctypes.Structure):
    _fields_=[('type',wintypes.WORD),('event',Event)]
k.WriteConsoleInputW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Record),wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
k.WriteConsoleInputW.restype=wintypes.BOOL
k.GenerateConsoleCtrlEvent.argtypes=[wintypes.DWORD,wintypes.DWORD]
k.GenerateConsoleCtrlEvent.restype=wintypes.BOOL
import msvcrt
async def main():
    with open('CONIN$','r',encoding='utf-8') as stream:
        session=PlainPromptSession(stream,io.StringIO())
        task=asyncio.create_task(session.prompt_async('> '))
        await asyncio.sleep(.05)
        codepoints=[0x7532,0xD83D,0xDE42,ord('x'),8,0x4FDD,13]
        records=(Record*len(codepoints))()
        for index,point in enumerate(codepoints):
            records[index].type=1
            records[index].event.key=Key(True,1,0,0,chr(point),0)
        count=wintypes.DWORD()
        if not k.WriteConsoleInputW(msvcrt.get_osfhandle(stream.fileno()),records,len(records),ctypes.byref(count)):
            raise ctypes.WinError(ctypes.get_last_error())
        text=await asyncio.wait_for(task,3)
        interrupted=asyncio.Event()
        previous=signal.getsignal(signal.SIGINT)
        cleanup=install_plain_interrupt(interrupted.set)
        try:
            if not k.GenerateConsoleCtrlEvent(0,0): raise ctypes.WinError(ctypes.get_last_error())
            await asyncio.wait_for(interrupted.wait(),3)
        finally: cleanup()
        session.close()
        print(json.dumps({'text':text,'restored':signal.getsignal(signal.SIGINT)==previous},ensure_ascii=True))
asyncio.run(main())
""", creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'text': '甲🙂保', 'restored': True})


if __name__ == '__main__':
    unittest.main()
