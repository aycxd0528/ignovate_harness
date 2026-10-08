"""Owned process trees and bounded pipe capture, including native Windows pipes."""
from __future__ import annotations

import asyncio
import base64
import codecs
import io
import os
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path

from nailong.tools.results import HeadTailBuffer


def command_environment():
    return {key: value for key, value in os.environ.items() if key != 'DEEPSEEK_API_KEY'}


def shell_command(command: str):
    """Use the platform's native shell without changing POSIX shell semantics."""
    if os.name != 'nt':
        return command, True
    # EncodedCommand preserves quotes, Unicode and newlines through CreateProcess.
    # Reset LASTEXITCODE so a successful PowerShell-only command exits with zero.
    script = ("$ProgressPreference = 'SilentlyContinue'; "
              "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
              "$OutputEncoding = [Console]::OutputEncoding; $global:LASTEXITCODE = 0; "
              + command + "\nif (-not $?) { if ($LASTEXITCODE) { exit $LASTEXITCODE }; exit 1 }; exit $LASTEXITCODE")
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    system_root = os.environ.get('SystemRoot', r'C:\Windows')
    powershell = str(Path(system_root) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe')
    return [powershell, '-NoLogo', '-NoProfile', '-NonInteractive', '-OutputFormat', 'Text', '-EncodedCommand', encoded], False


def split_editor_command(command: str) -> list[str]:
    if not command.strip():
        return []
    if os.name != 'nt':
        return shlex.split(command)
    # CommandLineToArgvW implements Windows quoting (unlike shlex's POSIX rules).
    import ctypes
    from ctypes import wintypes
    shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    parse = shell32.CommandLineToArgvW
    parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    free = kernel32.LocalFree
    free.argtypes, free.restype = [ctypes.c_void_p], ctypes.c_void_p
    count = ctypes.c_int()
    arguments = parse(command, ctypes.byref(count))
    if not arguments:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [arguments[index] for index in range(count.value)]
    finally:
        free(arguments)


class OwnedProcess:
    """A subprocess and the native ownership boundary for all its descendants."""

    def __init__(self, command, *, cwd=None, env=None, shell=False, **kwargs):
        self.job = None
        self.process = None
        if shell:
            command, shell = shell_command(command)
        if os.name == 'nt':
            from nailong.core._windows_process import WindowsJob
            self.job = WindowsJob()
            kwargs['creationflags'] = kwargs.get('creationflags', 0) | 0x4  # CREATE_SUSPENDED
        else:
            kwargs['start_new_session'] = True
        try:
            self.process = subprocess.Popen(command, cwd=cwd, env=env, shell=shell, **kwargs)
            if self.job:
                self.job.assign_and_resume(self.process)
        except BaseException:
            # Fail closed: a process that cannot join the job never runs user code.
            if self.process is not None:
                self.process.kill()
                self.process.wait()
                for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                    if stream is not None:
                        stream.close()
            if self.job:
                self.job.close()
            raise

    def terminate(self, *, force=True):
        if self.job:
            self.job.terminate()
        else:
            chosen_signal = signal.SIGKILL if force else signal.SIGTERM
            try:
                os.killpg(self.process.pid, chosen_signal)
            except PermissionError as error:
                # Darwin briefly retains an empty process group during exit,
                # returning EPERM rather than ESRCH. Reap the leader and retry;
                # a group with genuinely inaccessible descendants still fails.
                try:
                    self.process.wait(timeout=.1)
                except subprocess.TimeoutExpired:
                    raise error
                try:
                    os.killpg(self.process.pid, chosen_signal)
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass

    def close(self):
        try:
            self.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            if self.job:
                # TerminateJobObject is asynchronous; waiting for the leader
                # alone can leave descendants holding files or their cwd.
                self.job.wait_empty()
        finally:
            if self.job:
                self.job.close()


class _PrefixBuffer:
    def __init__(self, limit):
        self.limit, self.text, self.truncated = limit, '', False

    def append(self, text):
        available = max(0, self.limit-len(self.text))
        self.text += text[:available]
        self.truncated = self.truncated or len(text) > available

    def value(self):
        return self.text


class PipeCapture:
    """Drain pipes continuously with bounded storage; no pipe selectors required."""

    def __init__(self, command, *, cwd=None, env=None, shell=False,
                 max_output_chars=12000, separate_stderr=False, prefix=False, on_stdout=None,
                 universal_newlines=False):
        self.owner = OwnedProcess(command, cwd=cwd, env=env, shell=shell, bufsize=0,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE if separate_stderr else subprocess.STDOUT)
        self.process = self.owner.process
        self.universal_newlines = universal_newlines
        buffer_class = _PrefixBuffer if prefix else HeadTailBuffer
        self.stdout = buffer_class(max_output_chars)
        self.stderr = buffer_class(max_output_chars)
        self.threads = []
        self.errors = []
        for stream, output, callback in ((self.process.stdout, self.stdout, on_stdout),
                                         (self.process.stderr, self.stderr, None)):
            if stream is None:
                continue
            thread = threading.Thread(target=self._read, args=(stream, output, callback), daemon=True)
            thread.start()
            self.threads.append(thread)

    def _read(self, stream, output, callback):
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        if self.universal_newlines:
            decoder = io.IncrementalNewlineDecoder(decoder, translate=True)
        try:
            while True:
                chunk = stream.read(4096)
                text = decoder.decode(chunk, final=not chunk)
                output.append(text)
                if callback:
                    callback(text)
                if not chunk:
                    break
        except OSError as error:
            self.errors.append(error)
        finally:
            stream.close()

    @property
    def finished(self):
        return self.process.poll() is not None and all(not item.is_alive() for item in self.threads)

    def close(self):
        try:
            self.owner.close()
        finally:
            for thread in self.threads:
                thread.join(timeout=1)


def capture_streams(command, *, cwd=None, env=None, timeout=30, max_output_chars=12000,
                    separate_stderr=False, prefix=False, cancel_event=None, universal_newlines=False):
    capture = PipeCapture(command, cwd=cwd, env=env, shell=True,
        max_output_chars=max_output_chars, separate_stderr=separate_stderr, prefix=prefix,
        universal_newlines=universal_newlines)
    deadline, timed_out = time.monotonic()+timeout, False
    try:
        while not capture.finished:
            if cancel_event is not None and cancel_event.is_set():
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(.01)
    finally:
        capture.close()
    if capture.errors:
        raise capture.errors[0]
    return capture.stdout.value(), capture.stderr.value(), capture.stdout.truncated, timed_out, capture.process.returncode


def capture_command_output(command: str, *, cwd=None, env=None, timeout=30, max_output_chars=12000):
    stdout, _, truncated, timed_out, returncode = capture_streams(command, cwd=cwd, env=env,
        timeout=timeout, max_output_chars=max_output_chars)
    return stdout, truncated, timed_out, returncode


def capture_bytes(command, *, cwd=None, env=None, timeout=5, max_output_bytes=2_000_000, input_bytes=None):
    """Capture a bounded prefix. Timeout raises only after the tree is cleaned up."""
    owner = OwnedProcess(command, cwd=cwd, env=env, bufsize=0, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, stdin=subprocess.PIPE if input_bytes is not None else None)
    process, output = owner.process, bytearray()
    overflow = threading.Event()
    errors = []
    def read():
        try:
            while True:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                available = max(0, max_output_bytes-len(output))
                output.extend(chunk[:available])
                if len(chunk) > available:
                    overflow.set()
        except OSError as error:
            errors.append(error)
        finally:
            process.stdout.close()
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    writer = None
    if input_bytes is not None:
        def write():
            try:
                view = memoryview(input_bytes)
                while view:
                    count = process.stdin.write(view[:65536])
                    view = view[count:]
            except BrokenPipeError:
                pass
            except OSError as error:
                errors.append(error)
            finally:
                process.stdin.close()
        writer = threading.Thread(target=write, daemon=True)
        writer.start()
    deadline, timed_out = time.monotonic()+timeout, False
    try:
        while process.poll() is None or reader.is_alive():
            if overflow.is_set():
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(.01)
    finally:
        owner.close()
        reader.join(timeout=1)
        if writer:
            writer.join(timeout=1)
    if timed_out:
        raise subprocess.TimeoutExpired(command, timeout, output=bytes(output))
    if errors:
        raise errors[0]
    return bytes(output), process.returncode, overflow.is_set()


async def join_cleanup(task):
    """Join owned cleanup even under repeated cancellation; return cancellation state."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    return cancelled
