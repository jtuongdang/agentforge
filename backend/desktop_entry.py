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
    import uvicorn
    from app.remote_pairing import remote_boundary_enabled

    remote_listener = None
    remote_server = None
    remote_serving = None
    remote_port = None
    if remote_boundary_enabled():
        try:
            remote_port = int(os.getenv("CLINIC_ORCHESTRATOR_REMOTE_GATEWAY_PORT", "8766"))
        except ValueError as exc:
            raise ValueError("CLINIC_ORCHESTRATOR_REMOTE_GATEWAY_PORT must be an integer") from exc
        if not 1 <= remote_port <= 65535:
            raise ValueError("CLINIC_ORCHESTRATOR_REMOTE_GATEWAY_PORT must be between 1 and 65535")
        remote_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            remote_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            remote_listener.bind(("127.0.0.1", remote_port))
            remote_listener.listen(128)
            remote_listener.setblocking(False)
        except Exception:
            remote_listener.close()
            raise

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

    from app.main import app
    config = uvicorn.Config(app, host=bind_host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    if remote_listener:
        remote_config = uvicorn.Config(app, host="127.0.0.1", port=remote_port, log_level="warning",
                                       access_log=False, lifespan="off")
        remote_server = uvicorn.Server(remote_config)
        remote_serving = asyncio.create_task(remote_server.serve(sockets=[remote_listener]))
    while (not server.started or (remote_server is not None and not remote_server.started)) and not serving.done() \
            and not (remote_serving is not None and remote_serving.done()):
        await asyncio.sleep(0.025)
    if remote_serving is not None and remote_serving.done():
        await remote_serving
    if serving.done():
        await serving
        raise RuntimeError("Backend exited before startup completed")
    print(f"READY:{port}", flush=True)
    if remote_listener and remote_port is not None:
        print(f"REMOTE_READY:127.0.0.1:{remote_port}", flush=True)
    try:
        if remote_serving:
            await asyncio.gather(serving, remote_serving)
        else:
            await serving
    finally:
        server.should_exit = True
        if remote_server:
            remote_server.should_exit = True
        await asyncio.gather(*[task for task in (serving, remote_serving) if task is not None],
                             return_exceptions=True)
        if remote_listener:
            remote_listener.close()
        listener.close()


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
