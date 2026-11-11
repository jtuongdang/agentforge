"""Process-group helpers for stopping an Agent or validator and its descendants."""
from __future__ import annotations

import asyncio
import ctypes
import os
import signal
import subprocess
from ctypes import wintypes


def process_group_options() -> dict:
    if os.name == "posix":
        return {"start_new_session": True}
    return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}


class _JobBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JobIoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_size_t) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _JobExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimitInformation),
        ("IoInfo", _JobIoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _WindowsJob:
    """Kill all descendants when the Orchestrator closes this handle."""

    KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("Windows Job Objects are available only on Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                           ctypes.c_void_p, wintypes.DWORD]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self.handle = self._kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        limits = _JobExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = self.KILL_ON_JOB_CLOSE
        if not self._kernel32.SetInformationJobObject(self.handle, self.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                                       ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.close()
            raise OSError(error, "SetInformationJobObject failed")

    def assign(self, process: asyncio.subprocess.Process) -> None:
        popen = process._transport.get_extra_info("subprocess")
        handle = getattr(popen, "_handle", None)
        if handle is None or not self._kernel32.AssignProcessToJobObject(self.handle, handle):
            error = ctypes.get_last_error()
            raise OSError(error, "AssignProcessToJobObject failed")

    def close(self) -> None:
        handle = getattr(self, "handle", None)
        if handle:
            self._kernel32.CloseHandle(handle)
            self.handle = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


async def create_managed_process(*args, **kwargs) -> asyncio.subprocess.Process:
    """Create a process whose descendants can be stopped as one owned tree."""
    job = _WindowsJob() if os.name == "nt" else None
    process = None
    try:
        process = await asyncio.create_subprocess_exec(*args, **kwargs)
        if job:
            job.assign(process)
            process._devagent_job = job
        return process
    except Exception:
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        if job:
            job.close()
        raise


async def wait_for_process_exit(process: asyncio.subprocess.Process) -> int:
    """Wait for the root PID, without waiting for inherited pipes to reach EOF."""
    while process.returncode is None:
        await asyncio.sleep(0.02)
    return process.returncode


async def terminate_process_tree(process: asyncio.subprocess.Process, grace_seconds: float = 3.0) -> None:
    """Ask a subprocess tree to stop, then force-stop its process group if needed."""
    if not getattr(process, "pid", None):
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, 0)
        except (ProcessLookupError, PermissionError, OSError):
            return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        elif process.returncode is None:
            process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
    except (ProcessLookupError, PermissionError, OSError):
        pass
    if os.name == "posix":
        await asyncio.sleep(grace_seconds)
        # The group leader may already have exited while descendants still hold resources.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        if process.returncode is None:
            await process.wait()
    else:
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=grace_seconds)
            except asyncio.TimeoutError:
                pass
        job = getattr(process, "_devagent_job", None)
        if job:
            job.close()
        if process.returncode is None:
            process.kill()
            await process.wait()
