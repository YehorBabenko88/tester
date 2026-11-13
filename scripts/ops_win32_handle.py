"""Win32 adapter for original child-process handles.

Opt-in, Windows only. Receives a Popen created by the calling controller.
Never opens or adopts a foreign PID. No terminate(), kill(), SCM or UAC.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import subprocess

from scripts.ops_native_windows_witness import NativeRestartBlocked

STILL_ACTIVE = 259
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WAIT_FAILED = 0xFFFFFFFF
MAX_WAIT_MS = 60_000


class FILETIME(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    def ticks(self) -> int:
        return (int(self.high) << 32) | int(self.low)


class WindowsOriginalHandle:
    """Lifetime-bound handle belonging to exactly one Popen child."""

    def __init__(self, child: subprocess.Popen, *, kernel32=None):
        if os.name != "nt" and kernel32 is None:
            raise NativeRestartBlocked("Win32 process handles require Windows")
        if child.pid <= 0 or not hasattr(child, "_handle"):
            raise NativeRestartBlocked("An original Popen process handle is mandatory")
        self._child = child  # strong reference: prevents handle finalization
        self._handle = child._handle  # original handle; never OpenProcess(pid)
        if kernel32 is None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._api = kernel32
        self._api.GetProcessId.argtypes = [wintypes.HANDLE]
        self._api.GetProcessId.restype = wintypes.DWORD
        self._api.GetProcessTimes.argtypes = [wintypes.HANDLE,
                                             ctypes.POINTER(FILETIME),
                                             ctypes.POINTER(FILETIME),
                                             ctypes.POINTER(FILETIME),
                                             ctypes.POINTER(FILETIME)]
        self._api.GetProcessTimes.restype = wintypes.BOOL
        self._api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._api.WaitForSingleObject.restype = wintypes.DWORD
        self._api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self._api.GetExitCodeProcess.restype = wintypes.BOOL
        if int(self._api.GetProcessId(self._handle)) != child.pid:
            raise NativeRestartBlocked("Original Win32 handle does not belong to Popen PID")
        self.pid = child.pid
        self.created_100ns = self._creation_ticks()

    def _creation_ticks(self) -> int:
        creation, exit_time, kernel_time, user_time = (FILETIME() for _ in range(4))
        ok = self._api.GetProcessTimes(
            self._handle, ctypes.byref(creation), ctypes.byref(exit_time),
            ctypes.byref(kernel_time), ctypes.byref(user_time))
        if not ok or creation.ticks() <= 0:
            raise NativeRestartBlocked("Original Win32 process birth unavailable")
        return creation.ticks()

    def _verify(self) -> None:
        if (int(self._api.GetProcessId(self._handle)) != self.pid
                or self._child.pid != self.pid
                or self._creation_ticks() != self.created_100ns):
            raise NativeRestartBlocked("Win32 original handle ownership/birth changed")

    def poll(self) -> int | None:
        self._verify()
        status = self._api.WaitForSingleObject(self._handle, 0)
        if status == WAIT_TIMEOUT:
            return None
        if status != WAIT_OBJECT_0:
            raise NativeRestartBlocked("Win32 process wait failed")
        code = wintypes.DWORD()
        if not self._api.GetExitCodeProcess(self._handle, ctypes.byref(code)):
            raise NativeRestartBlocked("Win32 exit code unavailable")
        return int(code.value)

    def wait(self, timeout: float) -> int:
        if not 0 < timeout <= 60:
            raise NativeRestartBlocked("Bounded Win32 wait required")
        self._verify()
        status = self._api.WaitForSingleObject(self._handle, int(timeout * 1000))
        if status == WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired("owned_win32_process", timeout)
        if status != WAIT_OBJECT_0:
            raise NativeRestartBlocked("Win32 wait failed")
        code = self.poll()
        if code is None or code == STILL_ACTIVE:
            raise NativeRestartBlocked("Process exit not confirmed")
        return code
