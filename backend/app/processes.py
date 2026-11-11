"""Process-group helpers for stopping an Agent or validator and its descendants."""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess


def process_group_options() -> dict:
    if os.name == "posix":
        return {"start_new_session": True}
    return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}


async def terminate_process_tree(process: asyncio.subprocess.Process, grace_seconds: float = 3.0) -> None:
    """Ask a subprocess tree to stop, then force-stop its process group if needed."""
    if process.returncode is not None and os.name != "posix":
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
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
    elif process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
