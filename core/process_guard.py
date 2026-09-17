from __future__ import annotations

import atexit
import ctypes
import os
from ctypes import wintypes


# Windows Job Object limit that terminates all assigned processes when the
# last handle to the job is closed. This makes FFmpeg children follow LocalCam
# even when the launcher window is closed abruptly.
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_job_handle = None


if os.name == 'nt':
    ULONG_PTR = ctypes.c_size_t

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ('ReadOperationCount', ctypes.c_ulonglong),
            ('WriteOperationCount', ctypes.c_ulonglong),
            ('OtherOperationCount', ctypes.c_ulonglong),
            ('ReadTransferCount', ctypes.c_ulonglong),
            ('WriteTransferCount', ctypes.c_ulonglong),
            ('OtherTransferCount', ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ('PerProcessUserTimeLimit', ctypes.c_longlong),
            ('PerJobUserTimeLimit', ctypes.c_longlong),
            ('LimitFlags', wintypes.DWORD),
            ('MinimumWorkingSetSize', ULONG_PTR),
            ('MaximumWorkingSetSize', ULONG_PTR),
            ('ActiveProcessLimit', wintypes.DWORD),
            ('Affinity', ULONG_PTR),
            ('PriorityClass', wintypes.DWORD),
            ('SchedulingClass', wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ('BasicLimitInformation', _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ('IoInfo', _IO_COUNTERS),
            ('ProcessMemoryLimit', ULONG_PTR),
            ('JobMemoryLimit', ULONG_PTR),
            ('PeakProcessMemoryUsed', ULONG_PTR),
            ('PeakJobMemoryUsed', ULONG_PTR),
        ]


def install_kill_on_exit_job() -> None:
    """Assign LocalCam and its descendants to a Windows kill-on-close job.

    On normal shutdown LocalCam already stops FFmpeg itself. The Job Object is
    the safety net for launcher-window close, forced termination, crashes, or
    any other path where Python cannot run its normal shutdown code.
    """
    global _job_handle
    if os.name != 'nt' or _job_handle:
        return

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.INT,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        return

    limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

    ok = kernel32.SetInformationJobObject(
        handle,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    )
    if not ok:
        kernel32.CloseHandle(handle)
        return

    ok = kernel32.AssignProcessToJobObject(handle, kernel32.GetCurrentProcess())
    if not ok:
        # The Python process may already be managed by another Windows Job.
        # Keep LocalCam running normally and let its explicit shutdown paths
        # handle FFmpeg cleanup in that case.
        kernel32.CloseHandle(handle)
        return

    _job_handle = handle

    def close_job() -> None:
        global _job_handle
        if not _job_handle:
            return
        try:
            kernel32.CloseHandle(_job_handle)
        finally:
            _job_handle = None

    atexit.register(close_job)
