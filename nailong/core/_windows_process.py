"""Native Windows job ownership. Imported only on Windows."""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)


class _BasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64),
                ('PerJobUserTimeLimit', ctypes.c_int64), ('LimitFlags', wintypes.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t), ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wintypes.DWORD), ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
        'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _BasicAccounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in (
        'TotalUserTime', 'TotalKernelTime', 'ThisPeriodTotalUserTime', 'ThisPeriodTotalKernelTime')]
    _fields_ += [(name, wintypes.DWORD) for name in (
        'TotalPageFaultCount', 'TotalProcesses', 'ActiveProcesses', 'TotalTerminatedProcesses')]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimits), ('IoInfo', _IoCounters),
                ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                ('th32ThreadID', wintypes.DWORD), ('th32OwnerProcessID', wintypes.DWORD),
                ('tpBasePri', wintypes.LONG), ('tpDeltaPri', wintypes.LONG), ('dwFlags', wintypes.DWORD)]


def _bind(name, args, result):
    function = getattr(kernel32, name)
    function.argtypes, function.restype = args, result
    return function


_create_job = _bind('CreateJobObjectW', [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE)
_set_job = _bind('SetInformationJobObject', [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL)
_query_job = _bind('QueryInformationJobObject', [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                               wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
_assign = _bind('AssignProcessToJobObject', [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL)
_open_process = _bind('OpenProcess', [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE)
_terminate = _bind('TerminateJobObject', [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL)
_wait_process = _bind('WaitForSingleObject', [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD)
_in_job = _bind('IsProcessInJob', [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL)
_close = _bind('CloseHandle', [wintypes.HANDLE], wintypes.BOOL)
_snapshot = _bind('CreateToolhelp32Snapshot', [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE)
_first_thread = _bind('Thread32First', [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL)
_next_thread = _bind('Thread32Next', [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL)
_open_thread = _bind('OpenThread', [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE)
_resume_thread = _bind('ResumeThread', [wintypes.HANDLE], wintypes.DWORD)


class WindowsJob:
    """A non-inheritable kill-on-close job with no child breakaway permission."""

    def __init__(self):
        self._process_handles = {}
        self.handle = _create_job(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _set_job(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign_and_resume(self, process):
        # CREATE_SUSPENDED ensures the primary thread cannot spawn children
        # while we assign ownership and obtain a Toolhelp/OpenThread handle.
        # Own and close this process handle independently of Popen internals.
        native_process = _open_process(0x101, False, process.pid)  # SET_QUOTA | TERMINATE
        if not native_process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not _assign(self.handle, native_process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _close(native_process)
        snapshot = _snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            present = _first_thread(snapshot, ctypes.byref(entry))
            while present:
                if entry.th32OwnerProcessID == process.pid:
                    thread = _open_thread(2, False, entry.th32ThreadID)  # THREAD_SUSPEND_RESUME
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if _resume_thread(thread) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                        return
                    finally:
                        _close(thread)
                present = _next_thread(snapshot, ctypes.byref(entry))
            raise OSError('Cannot locate the suspended process primary thread.')
        finally:
            _close(snapshot)

    def _pin_processes(self):
        """Keep process objects alive through termination, including cwd cleanup."""
        capacity = 64
        while self.handle:
            class ProcessIds(ctypes.Structure):
                _fields_ = [('assigned', wintypes.DWORD), ('count', wintypes.DWORD),
                            ('pids', ctypes.c_size_t * capacity)]
            members = ProcessIds()
            if _query_job(self.handle, 3, ctypes.byref(members), ctypes.sizeof(members), None):
                break
            error = ctypes.get_last_error()
            if error != 234:  # ERROR_MORE_DATA: membership grew during the query.
                raise ctypes.WinError(error)
            capacity = max(capacity * 2, members.assigned)
        else:
            return
        for pid in members.pids[:members.count]:
            if pid in self._process_handles:
                continue
            # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION. A PID alone or
            # ActiveProcesses == 0 cannot prove the process released its cwd.
            handle = _open_process(0x100000 | 0x1000, False, pid)
            if not handle:
                error = ctypes.get_last_error()
                if error == 87:  # The process object already disappeared.
                    continue
                raise ctypes.WinError(error)
            keep = False
            try:
                member = wintypes.BOOL()
                if not _in_job(handle, self.handle, ctypes.byref(member)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if member.value:
                    self._process_handles[pid] = handle
                    keep = True
            finally:
                if not keep:
                    _close(handle)

    def terminate(self):
        if self.handle:
            # Close the creation window before collecting handles. Existing
            # members may exit, but a parent cannot add another active child.
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000 | 8
            limits.BasicLimitInformation.ActiveProcessLimit = 1
            if not _set_job(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            self._pin_processes()
            if not _terminate(self.handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            # Include a child created between the first snapshot and termination.
            self._pin_processes()

    def wait_empty(self, timeout=5):
        """Wait for process objects to signal, rather than only job accounting."""
        deadline = time.monotonic() + timeout
        while self.handle:
            self._pin_processes()
            for handle in self._process_handles.values():
                remaining = max(0, deadline - time.monotonic())
                result = _wait_process(handle, min(0xFFFFFFFE, int(remaining * 1000)))
                if result == 258:  # WAIT_TIMEOUT
                    raise TimeoutError('Windows job descendants did not finish terminating.')
                if result == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                if result != 0:
                    raise OSError('Unexpected Windows process wait result.')
            accounting = _BasicAccounting()
            if not _query_job(self.handle, 1, ctypes.byref(accounting),
                              ctypes.sizeof(accounting), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if accounting.ActiveProcesses == 0:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Windows job descendants did not finish terminating.')
            time.sleep(min(.01, remaining))

    def close(self):
        handles, self._process_handles = self._process_handles, {}
        for handle in handles.values():
            _close(handle)
        if self.handle:
            handle, self.handle = self.handle, None
            _close(handle)
