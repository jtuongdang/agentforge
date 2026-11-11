"""Standalone backend entry point used by the Tauri desktop shell."""
from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))


async def serve() -> None:
    bind_host = os.getenv("CLINIC_ORCHESTRATOR_HOST", "127.0.0.1")
    listener = socket.socket(socket.AF_INET6 if ":" in bind_host else socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((bind_host, 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    os.environ["CLINIC_ORCHESTRATOR_HOST"] = bind_host
    os.environ["CLINIC_ORCHESTRATOR_PORT"] = str(port)
    os.environ["CLINIC_ORCHESTRATOR_HEADLESS"] = "1"

    import uvicorn
    from app.main import app
    config = uvicorn.Config(app, host=bind_host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started and not serving.done():
        await asyncio.sleep(0.025)
    if serving.done():
        await serving
        raise RuntimeError("Backend exited before startup completed")
    print(f"READY:{port}", flush=True)
    await serving


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--process-watchdog":
        from app.processes import process_watchdog_main
        process_watchdog_main(int(sys.argv[2]))
        return
    if not sys.argv[0].lower().endswith(".py"):
        os.environ.setdefault("CLINIC_ORCHESTRATOR_WATCHDOG_EXECUTABLE", str(Path(sys.argv[0]).resolve()))
    asyncio.run(serve())


if __name__ == "__main__":
    main()
