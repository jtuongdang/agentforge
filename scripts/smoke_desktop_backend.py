#!/usr/bin/env python3
"""Run the packaged backend sidecar and verify its authenticated local API."""
from __future__ import annotations

import json
import os
import queue
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path
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
        env = os.environ.copy()
        env["CLINIC_ORCHESTRATOR_DATA_DIR"] = str(temp / "data")
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
                    break
            if port is None:
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

            database = temp / "data" / "orchestrator.sqlite3"
            if not database.is_file():
                raise RuntimeError("Packaged sidecar did not create its application database")
            with closing(sqlite3.connect(database)) as con:
                version = con.execute("PRAGMA user_version").fetchone()[0]
                integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            if version < 6 or integrity != "ok":
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
            print(f"Packaged sidecar smoke passed: API/token auth, OS credential store, schema={version}, provider metadata, checkpoint export/restore.")
            return 0
        except Exception as exc:
            print(f"Packaged sidecar smoke failed: {exc}", file=sys.stderr)
            return 1
        finally:
            terminate_tree(process)


if __name__ == "__main__":
    raise SystemExit(main())
