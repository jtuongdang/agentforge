"""Process-group helpers for stopping an Agent or validator and its descendants."""
from __future__ import annotations

import asyncio
import ctypes
import os
import signal
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path


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
    posix_guard = None
    process_group_id = None
    process = None
    try:
        if os.name == "posix":
            kwargs.pop("start_new_session", None)
            kwargs.pop("process_group", None)
            read_fd, write_fd = os.pipe()
            os.set_inheritable(read_fd, True)
            posix_guard = _PosixProcessGuard(write_fd)
            packaged_watchdog = os.getenv("CLINIC_ORCHESTRATOR_WATCHDOG_EXECUTABLE")
            if packaged_watchdog:
                watchdog_command = [packaged_watchdog, "--process-watchdog", str(read_fd)]
            else:
                watchdog_code = ("import os,sys; sys.path.insert(0,sys.argv[2]); "
                                 "from app.processes import process_watchdog_main; "
                                 "process_watchdog_main(int(sys.argv[1]))")
                watchdog_command = [sys.executable, "-c", watchdog_code, str(read_fd),
                                    str(Path(__file__).resolve().parents[1])]
            try:
                guardian = await asyncio.create_subprocess_exec(
                    *watchdog_command, pass_fds=(read_fd,), process_group=0,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            finally:
                os.close(read_fd)
            process_group_id = guardian.pid
            posix_guard.set_process_group_id(process_group_id)
            kwargs["process_group"] = process_group_id
        process = await asyncio.create_subprocess_exec(*args, **kwargs)
        if job:
            job.assign(process)
            process._devagent_job = job
        if posix_guard:
            process._devagent_posix_guard = posix_guard
            process._devagent_process_group_id = process_group_id
        return process
    except Exception:
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        if job:
            job.close()
        if posix_guard:
            posix_guard.close()
        raise


async def wait_for_process_exit(process: asyncio.subprocess.Process) -> int:
    """Wait for the root PID, without waiting for inherited pipes to reach EOF."""
    while process.returncode is None:
        await asyncio.sleep(0.02)
    return process.returncode


def process_watchdog_main(read_fd: int) -> None:
    """Read the owned PGID, wait for owner pipe close, then kill that group."""
    if os.name != "posix":
        return
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    group_buffer = bytearray()
    try:
        while True:
            try:
                data = os.read(read_fd, 4096)
            except InterruptedError:
                continue
            if not data:
                break
            group_buffer.extend(data)
            if b"\n" in group_buffer:
                break
        if b"\n" in group_buffer:
            raw_group, _, remainder = group_buffer.partition(b"\n")
            group_id = int(raw_group)
            if remainder:
                raise ValueError("Unexpected process watchdog bootstrap data")
            while True:
                try:
                    if not os.read(read_fd, 4096):
                        break
                except InterruptedError:
                    continue
        else:
            group_id = os.getpid()
    finally:
        os.close(read_fd)
    try:
        os.killpg(group_id, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return
    time.sleep(0.25)
    try:
        os.killpg(group_id, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


class _PosixProcessGuard:
    """Closing the pipe asks the independent guardian to kill its process group."""

    def __init__(self, fd: int):
        self.fd = fd

    def set_process_group_id(self, process_group_id: int) -> None:
        fd = getattr(self, "fd", None)
        if fd is None:
            raise RuntimeError("Process guard is already closed")
        payload = f"{process_group_id}\n".encode("ascii")
        written = os.write(fd, payload)
        if written != len(payload):
            raise OSError("Could not send process group id to watchdog")

    def close(self) -> None:
        fd = getattr(self, "fd", None)
        if fd is not None:
            self.fd = None
            try:
                os.close(fd)
            except OSError:
                pass

    def __del__(self):
        self.close()


async def terminate_process_tree(process: asyncio.subprocess.Process, grace_seconds: float = 3.0) -> None:
    """Ask a subprocess tree to stop, then force-stop its process group if needed."""
    if not getattr(process, "pid", None):
        return
    if os.name == "posix":
        process_group_id = getattr(process, "_devagent_process_group_id", process.pid)
        try:
            os.killpg(process_group_id, 0)
        except (ProcessLookupError, PermissionError, OSError):
            guard = getattr(process, "_devagent_posix_guard", None)
            if guard:
                guard.close()
            return
    try:
        if os.name == "posix":
            os.killpg(process_group_id, signal.SIGTERM)
        elif process.returncode is None:
            process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
    except (ProcessLookupError, PermissionError, OSError):
        pass
    if os.name == "posix":
        await asyncio.sleep(grace_seconds)
        # The group leader may already have exited while descendants still hold resources.
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        guard = getattr(process, "_devagent_posix_guard", None)
        if guard:
            guard.close()
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
