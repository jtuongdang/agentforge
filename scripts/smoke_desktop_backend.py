#!/usr/bin/env python3
"""Run the packaged backend sidecar and verify its authenticated local API."""
from __future__ import annotations

import json
import os
import queue
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path
from socket import AF_INET, SOCK_STREAM, socket
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def terminate_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, 0)
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, check=False, timeout=10)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def read_json(url: str, headers: dict[str, str] | None = None) -> dict:
    request = Request(url, headers=headers or {})
    with urlopen(request, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"Local API returned HTTP {response.status}")
        return json.loads(response.read())


def post_json(url: str, payload: dict, headers: dict[str, str], expected_status: int = 200) -> dict:
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers={
        **headers, "Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=30) as response:
        if response.status != expected_status:
            raise RuntimeError(f"Local API returned HTTP {response.status}")
        return json.loads(response.read())


def verify_packaged_crash_guard(binary: Path, temp: Path) -> None:
    if os.name != "posix":
        return
    backend_root = Path(__file__).resolve().parents[1] / "backend"
    ready = temp / "guardian-owner-ready"
    marker = temp / "guardian-worker-survived-owner"
    owner_code = (
        "import asyncio,os,pathlib,sys; sys.path.insert(0,sys.argv[1]); "
        "os.environ['CLINIC_ORCHESTRATOR_WATCHDOG_EXECUTABLE']=sys.argv[2]; "
        "from app.processes import create_managed_process,process_group_options\n"
        "async def main():\n"
        "    child=\"import pathlib,sys,time; time.sleep(1.0); pathlib.Path(sys.argv[1]).write_text('survived')\"\n"
        "    await create_managed_process(sys.executable,'-c',child,%r,stdout=asyncio.subprocess.DEVNULL,"
        "stderr=asyncio.subprocess.DEVNULL,**process_group_options())\n"
        "    pathlib.Path(sys.argv[3]).write_text('ready')\n"
        "    os._exit(0)\n"
        "asyncio.run(main())" % (str(marker),)
    )
    owner = subprocess.Popen([sys.executable, "-c", owner_code, str(backend_root), str(binary), str(ready)],
                             cwd=backend_root, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 15
    while not ready.exists() and owner.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    _, error = owner.communicate(timeout=5)
    if owner.returncode != 0 or not ready.is_file():
        raise RuntimeError("Packaged crash guardian failed to start: " + (error or "owner exited early")[-2000:])
    time.sleep(1.3)
    if marker.exists():
        raise RuntimeError("Worker survived after its owner process crashed")


@contextmanager
def temporary_workspace():
    """Retry Windows temp cleanup while the terminated sidecar releases files."""
    temp = tempfile.TemporaryDirectory(prefix="devagent-sidecar-smoke-")
    try:
        yield Path(temp.name)
    finally:
        deadline = time.monotonic() + 10
        while True:
            try:
                temp.cleanup()
                break
            except PermissionError:
                if os.name != "nt" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: smoke_desktop_backend.py PATH_TO_PACKAGED_SIDECAR", file=sys.stderr)
        return 2
    binary = Path(sys.argv[1]).expanduser().resolve(strict=True)
    if not binary.is_file():
        print(f"Sidecar is not a file: {binary}", file=sys.stderr)
        return 2

    messages: queue.Queue[str | None] = queue.Queue()
    tail: list[str] = []
    with temporary_workspace() as temp:
        verify_packaged_crash_guard(binary, temp)
        env = os.environ.copy()
        env["CLINIC_ORCHESTRATOR_DATA_DIR"] = str(temp / "data")
        env["CLINIC_ORCHESTRATOR_REMOTE_ORIGIN"] = "https://remote.example.invalid"
        env["CLINIC_ORCHESTRATOR_REMOTE_PROXY_IPS"] = "127.0.0.1"
        with socket(AF_INET, SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            remote_port = probe.getsockname()[1]
        env["CLINIC_ORCHESTRATOR_REMOTE_GATEWAY_PORT"] = str(remote_port)
        process = subprocess.Popen(
            [str(binary)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=env, start_new_session=(os.name == "posix"),
            creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0),
        )

        def collect_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                line = line.rstrip()
                tail.append(line[:500])
                del tail[:-20]
                messages.put(line)
            messages.put(None)

        threading.Thread(target=collect_output, daemon=True).start()
        try:
            deadline = time.monotonic() + 45
            port = None
            gateway_port = None
            while time.monotonic() < deadline:
                try:
                    line = messages.get(timeout=0.25)
                except queue.Empty:
                    if process.poll() is not None:
                        break
                    continue
                if line is None:
                    break
                if line.startswith("READY:"):
                    port = int(line.partition(":")[2])
                elif line.startswith("REMOTE_READY:"):
                    gateway_port = int(line.rpartition(":")[2])
                if port is not None and gateway_port is not None:
                    break
            if port is None or gateway_port is None:
                raise RuntimeError("Sidecar failed to report readiness: " + " | ".join(tail))

            base = f"http://127.0.0.1:{port}"
            session = read_json(base + "/api/session", {"Origin": "tauri://localhost"})
            token = session.get("access_token")
            if not isinstance(token, str) or len(token) < 32:
                raise RuntimeError("Packaged sidecar did not provide a valid local session token")
            fleet = read_json(base + "/api/fleet", {"Authorization": f"Bearer {token}"})
            if not isinstance(fleet, dict) or "settings" not in fleet:
                raise RuntimeError("Authenticated Fleet API returned an unexpected response")
            credential_store = read_json(base + "/api/providers/credential-store", {"Authorization": f"Bearer {token}"})
            if not credential_store.get("available"):
                raise RuntimeError("Packaged sidecar has no approved OS credential store: "
                                   + str(credential_store.get("reason", "unavailable")))
            remote_status = read_json(base + "/api/remote/status", {"Authorization": f"Bearer {token}"})
            if not remote_status.get("enabled") or remote_status.get("gateway") != f"127.0.0.1:{gateway_port}":
                raise RuntimeError("Packaged sidecar did not enable the configured loopback remote gateway")

            ingress_headers = {"Host": "remote.example.invalid", "X-Forwarded-Proto": "https"}
            with urlopen(Request(f"http://127.0.0.1:{gateway_port}/remote", headers=ingress_headers), timeout=5) as response:
                remote_html = response.read().decode("utf-8")
                if response.status != 200 or 'src="/remote/assets/' not in remote_html:
                    raise RuntimeError("Packaged remote browser viewer did not load from the gateway")
            if 'href="/remote/assets/favicon.svg"' not in remote_html:
                raise RuntimeError("Remote viewer HTML does not route its favicon through the allowed prefix")
            with urlopen(Request(f"http://127.0.0.1:{gateway_port}/remote/assets/favicon.svg",
                                 headers=ingress_headers), timeout=5) as response:
                if response.status != 200 or b"<svg" not in response.read():
                    raise RuntimeError("Packaged remote viewer favicon did not load")
            asset = re.search(r'src="/remote/assets/([^\"]+)"', remote_html)
            if asset is None:
                raise RuntimeError("Remote viewer HTML does not reference gateway-scoped assets")
            with urlopen(Request(f"http://127.0.0.1:{gateway_port}/remote/assets/{asset.group(1)}",
                                 headers=ingress_headers), timeout=5) as response:
                asset_content = response.read()
                if response.status != 200 or b"DevAgent Remote Review" not in asset_content:
                    raise RuntimeError("Packaged remote viewer asset did not load")
            try:
                urlopen(Request(f"http://127.0.0.1:{gateway_port}/api/fleet",
                                headers=ingress_headers | {"Authorization": f"Bearer {token}"}), timeout=5)
                raise RuntimeError("Owner API was reachable through the remote gateway")
            except HTTPError as exc:
                if exc.code != 403:
                    raise RuntimeError(f"Remote gateway owner API returned unexpected HTTP {exc.code}") from exc

            database = temp / "data" / "orchestrator.sqlite3"
            if not database.is_file():
                raise RuntimeError("Packaged sidecar did not create its application database")
            with closing(sqlite3.connect(database)) as con:
                version = con.execute("PRAGMA user_version").fetchone()[0]
                integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
                remote_tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
                                 if row[0].startswith("remote_")}
            if version < 8 or integrity != "ok" or not {
                    "remote_invites", "remote_pairing_requests", "remote_devices", "remote_task_diff_grants"}.issubset(remote_tables):
                raise RuntimeError(f"Packaged database failed checks: schema={version}, integrity={integrity}")
            headers = {"Authorization": f"Bearer {token}"}
            provider = post_json(base + "/api/providers", {
                "provider": "ollama", "name": "Packaged local fixture"}, headers, expected_status=201)
            if provider.get("has_credential") or "secret_ref" in provider or "api_key" in provider:
                raise RuntimeError("Provider API returned credential material in its metadata")
            provider_url = base + "/api/providers/" + provider["id"]
            with urlopen(Request(provider_url, headers=headers, method="DELETE"), timeout=5) as response:
                if response.status != 204:
                    raise RuntimeError("Packaged provider connection could not be removed")
            checkpoint = post_json(base + "/api/storage/checkpoints", {"include_uncommitted": False}, headers)
            checkpoint_path = temp / "data" / "checkpoints" / checkpoint["filename"]
            if not checkpoint_path.is_file():
                raise RuntimeError("Packaged sidecar did not create its checkpoint archive")
            restore_parent = temp / "restored"
            restore_parent.mkdir()
            restored = post_json(base + "/api/storage/checkpoints/restore", {
                "checkpoint_path": str(checkpoint_path), "destination_parent": str(restore_parent)}, headers)
            restored_database = Path(restored["database_path"])
            if not restored_database.is_file() or not (Path(restored["path"]) / "RESTORE-README.txt").is_file():
                raise RuntimeError("Packaged sidecar did not produce a complete restored profile")
            with closing(sqlite3.connect(restored_database)) as con:
                restored_integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            if restored_integrity != "ok" or restored["repository_count"] != 0:
                raise RuntimeError("Packaged restored profile failed database or repository checks")
            print(f"Packaged sidecar smoke passed: owner API isolation, remote viewer/assets, OS credential store, schema={version}, provider metadata, checkpoint export/restore, process crash containment.")
            return 0
        except Exception as exc:
            print(f"Packaged sidecar smoke failed: {exc}", file=sys.stderr)
            return 1
        finally:
            terminate_tree(process)


if __name__ == "__main__":
    raise SystemExit(main())
