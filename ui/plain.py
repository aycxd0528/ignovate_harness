"""Plain adapter: asynchronous line input without full-screen terminal control."""
from __future__ import annotations
import asyncio
import os
import sys
from dataclasses import replace
from rich.console import Console as RichConsole
from ui.console import Console
from ui.theme import load_theme

def install_plain_interrupt(callback):
    """Schedule Ctrl+C on this loop and restore its previous Python handler."""
    import signal
    loop = asyncio.get_running_loop()
    previous = signal.getsignal(signal.SIGINT)
    if os.name == 'nt':
        def handler(_signal, _frame):
            loop.call_soon_threadsafe(callback)
        signal.signal(signal.SIGINT, handler)
    else:
        loop.add_signal_handler(signal.SIGINT, callback)
    def restore():
        if os.name != 'nt':
            loop.remove_signal_handler(signal.SIGINT)
        signal.signal(signal.SIGINT, previous)
    return restore


class _WindowsInput:
    """Poll console events or available pipe bytes without a blocking reader."""
    def __init__(self, fd):
        import codecs
        import ctypes
        import msvcrt
        from ctypes import wintypes
        self.ctypes, self.types = ctypes, wintypes
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.fd = os.dup(fd)
        try:
            # CRT text mode may look ahead after a trailing CR and block even
            # after PeekNamedPipe says bytes are available. Change only our dup.
            msvcrt.setmode(self.fd, os.O_BINARY)
            self.handle = msvcrt.get_osfhandle(self.fd)
            def bind(name, arguments, result):
                function = getattr(self.kernel, name)
                function.argtypes, function.restype = arguments, result
                return function
            get_mode = bind('GetConsoleMode', [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
            self.console = bool(get_mode(self.handle, ctypes.byref(wintypes.DWORD())))
            self.file_type = bind('GetFileType', [wintypes.HANDLE], wintypes.DWORD)(self.handle)
            if self.console:
                class Key(ctypes.Structure):
                    _fields_ = [('down', wintypes.BOOL), ('repeat', wintypes.WORD),
                                ('key', wintypes.WORD), ('scan', wintypes.WORD),
                                ('char', wintypes.WCHAR), ('state', wintypes.DWORD)]
                class Event(ctypes.Union):
                    _fields_ = [('key', Key), ('padding', ctypes.c_byte*16)]
                class Record(ctypes.Structure):
                    _fields_ = [('type', wintypes.WORD), ('event', Event)]
                self.Record = Record
                self.events = bind('GetNumberOfConsoleInputEvents',
                    [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
                self.read_events = bind('ReadConsoleInputW',
                    [wintypes.HANDLE, ctypes.POINTER(Record), wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
                self.decoder = codecs.getincrementaldecoder('utf-16-le')(errors='replace')
            elif self.file_type == 3:  # FILE_TYPE_PIPE, including anonymous stdin pipes
                self.peek = bind('PeekNamedPipe', [wintypes.HANDLE, ctypes.c_void_p,
                    wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p], wintypes.BOOL)
            elif self.file_type != 1:  # FILE_TYPE_DISK
                raise OSError('Plain input requires a console, pipe or regular input file.')
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise

    def read_ready(self):
        c, t = self.ctypes, self.types
        if self.console:
            available = t.DWORD()
            if not self.events(self.handle, c.byref(available)):
                raise c.WinError(c.get_last_error())
            if not available.value:
                return None
            records = (self.Record*min(available.value, 64))()
            count = t.DWORD()
            if not self.read_events(self.handle, records, len(records), c.byref(count)):
                raise c.WinError(c.get_last_error())
            text = ''.join(record.event.key.char*record.event.key.repeat
                for record in records[:count.value]
                if record.type == 1 and record.event.key.down and record.event.key.char != '\0')
            decoded = self.decoder.decode(text.encode('utf-16-le', errors='surrogatepass'))
            return decoded or None
        if self.file_type == 3:
            available = t.DWORD()
            if not self.peek(self.handle, None, 0, None, c.byref(available), None):
                error = c.get_last_error()
                if error in {109, 232, 233}:  # Broken, closed or disconnected pipe
                    return b''
                raise c.WinError(error)
            if not available.value:
                return None
            return os.read(self.fd, min(available.value, 4096))
        return os.read(self.fd, 4096)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class PlainPromptSession:
    def __init__(self, input_stream=None, output_stream=None):
        self.input = input_stream or sys.stdin
        self.output = output_stream or sys.stdout
        self.buffer, self.eof, self.closed = b'', False, False
        self._reader_loop = self._reader_fd = self._waiter = None

    def close(self):
        """Stop pending reads while leaving the caller's input stream open."""
        self.closed = True
        if self._reader_loop is not None:
            self._reader_loop.remove_reader(self._reader_fd)
        if self._waiter is not None and not self._waiter.done():
            self._waiter.set_result(None)

    async def _read_posix(self, fd):
        loop = asyncio.get_running_loop()
        ready = loop.create_future()
        self._reader_loop, self._reader_fd, self._waiter = loop, fd, ready
        def readable():
            if self.closed:
                if not ready.done(): ready.set_result(None)
                return
            try:
                chunk = os.read(fd, 4096)
                self.buffer += chunk
                if not chunk: self.eof = True
                if not ready.done(): ready.set_result(None)
            except OSError as error:
                if not ready.done(): ready.set_exception(error)
        try:
            loop.add_reader(fd, readable)
            await ready
        finally:
            loop.remove_reader(fd)
            self._reader_loop = self._reader_fd = self._waiter = None

    def _console_text(self, text):
        from rich.cells import cell_len
        from ui.input_broker import InputInterrupted
        encoding = self.input.encoding or 'utf-8'
        for character in text:
            if character == '\x03':
                self.buffer = b''
                self.output.write('^C\n')
                self.output.flush()
                raise InputInterrupted()
            if character == '\x1a':
                self.eof = True
                break
            if character == '\b':
                current = self.buffer.decode(encoding, errors='replace')
                if current and not current.endswith('\n'):
                    self.buffer = current[:-1].encode(encoding, errors='replace')
                    self.output.write('\b \b'*max(1, cell_len(current[-1])))
            elif character in {'\r', '\n'}:
                self.buffer += b'\n'
                self.output.write('\n')
            elif character == '\t' or character >= ' ':
                self.buffer += character.encode(encoding, errors='replace')
                self.output.write(character)
        self.output.flush()

    async def prompt_async(self, message, *, default=None, bottom_toolbar=None):
        if self.closed:
            raise EOFError()
        self.output.write(message+(f'[保留输入：{default}] ' if message == '> ' and default else ''))
        self.output.flush()
        fd = self.input.fileno()
        native = _WindowsInput(fd) if os.name == 'nt' else None
        try:
            while b'\n' not in self.buffer:
                if self.closed:
                    raise EOFError()
                if self.eof:
                    if self.buffer: break
                    raise EOFError()
                if native is None:
                    await self._read_posix(fd)
                else:
                    chunk = native.read_ready()
                    if chunk is None:
                        await asyncio.sleep(.025)
                    elif native.console:
                        self._console_text(chunk)
                    else:
                        self.buffer += chunk
                        if not chunk: self.eof = True
            line, sep, self.buffer = self.buffer.partition(b'\n')
            text = line.decode(self.input.encoding or 'utf-8', errors='replace').rstrip('\r')
            return text or default or ''
        finally:
            if native is not None:
                native.close()

class PlainConsole(Console):
    def __init__(self):
        super().__init__(theme=load_theme(ascii_only=True),console=RichConsole(no_color=True,highlight=False))
    def apply_theme(self, theme):
        super().apply_theme(replace(theme, glyph_running=">", glyph_ok="+", glyph_fail="!", gutter="|", bullet="-"))
    def resume_live(self): pass
    def pause_live(self): pass

async def run_plain(settings,**options):
    from ui.app import run_inline
    session = PlainPromptSession()
    try:
        await run_inline(settings, prompt_session=session, console=PlainConsole(), plain=True, **options)
    finally:
        session.close()
