"""Real process fixtures expressed in each platform's native command syntax."""
import os
import shlex
import subprocess
import sys
from pathlib import Path


def process_exists(pid):
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value == 259
    finally:
        kernel.CloseHandle(handle)


def kill_process_if_alive(pid):
    if not process_exists(pid):
        return
    if os.name == 'nt':
        import subprocess
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, timeout=10)
    else:
        import signal
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def assert_private(test, path, *, directory=False):
    """Verify real user privacy through POSIX modes or a protected Windows DACL."""
    if os.name != 'nt':
        test.assertEqual(Path(path).stat().st_mode & 0o777, 0o700 if directory else 0o600)
        return
    import win32api
    import win32con
    import win32security
    descriptor = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
    test.assertTrue(descriptor.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED)
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    expected = {win32security.ConvertSidToStringSid(user), 'S-1-5-18', 'S-1-5-32-544'}
    acl = descriptor.GetSecurityDescriptorDacl()
    actual = set()
    for index in range(acl.GetAceCount()):
        header, mask, sid = acl.GetAce(index)
        test.assertEqual(header[0], win32security.ACCESS_ALLOWED_ACE_TYPE)
        test.assertEqual(mask, 0x1F01FF)
        actual.add(win32security.ConvertSidToStringSid(sid))
    test.assertEqual(actual, expected)


def shell_join(arguments):
    values = list(arguments)
    if os.name != 'nt':
        return shlex.join(values)
    if values[0] == sys.executable and '-c' in values:
        index = values.index('-c') + 1
        source = values[index]
        values[index] = "exec(bytes.fromhex('" + source.encode('utf-8').hex() + "').decode('utf-8'))"
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    return '& ' + ' '.join(map(quote, values))


def python_command(source):
    return shell_join([sys.executable, '-B', '-u', '-c', source])


def editor_command(arguments):
    return subprocess.list2cmdline(arguments) if os.name == 'nt' else shlex.join(arguments)
