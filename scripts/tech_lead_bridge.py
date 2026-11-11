#!/usr/bin/env python3
"""Loopback-only client for the Orchestrator's scoped Tech Lead session."""
from __future__ import annotations

import argparse
import getpass
import json
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx


DEFAULT_SCOPES = ("handover:read", "task:create", "task:review")


class BridgeError(RuntimeError):
    pass


def validate_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise BridgeError("Tech Lead bridge base URL is invalid") from None
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise BridgeError("Tech Lead bridge accepts only an HTTP(S) loopback base URL")
    return value.rstrip("/")


class TechLeadBridge:
    """Scoped Tech Lead API client; access token is never persisted or printed."""

    def __init__(self, base_url: str, client_name: str = "Local Tech Lead CLI", client_id: str | None = None,
                 requested_scopes: tuple[str, ...] = DEFAULT_SCOPES,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = validate_base_url(base_url)
        self.client_name = client_name[:160]
        self.client_id = (client_id or f"local-cli-{uuid.uuid4().hex}")[:160]
        self.requested_scopes = list(requested_scopes)
        self._token: str | None = None
        self._client = httpx.Client(base_url=self.base_url, timeout=8.0, follow_redirects=False,
                                    trust_env=False, transport=transport)

    def close(self) -> None:
        self._token = None
        self._client.close()

    def _request(self, method: str, path: str, *, json_body: dict | None = None) -> dict:
        if not self._token:
            raise BridgeError("Pair the Tech Lead bridge before calling scoped endpoints")
        try:
            response = self._client.request(method, path, json=json_body,
                headers={"Authorization": f"Bearer {self._token}"})
        except httpx.HTTPError as exc:
            raise BridgeError(f"Tech Lead request failed ({type(exc).__name__})") from None
        if response.is_redirect:
            raise BridgeError("Tech Lead server attempted a redirect; request was stopped")
        if response.status_code >= 300:
            raise BridgeError(f"Tech Lead request was rejected (HTTP {response.status_code})")
        try:
            result = response.json()
        except (ValueError, json.JSONDecodeError):
            raise BridgeError("Tech Lead server returned invalid JSON") from None
        if not isinstance(result, dict):
            raise BridgeError("Tech Lead server returned an unexpected response")
        return result

    def pair(self, pairing_code: str) -> dict:
        try:
            response = self._client.post("/api/lead/handshake", json={
                "pairing_code": pairing_code, "client_id": self.client_id,
                "client_name": self.client_name, "requested_scopes": self.requested_scopes,
            })
        except httpx.HTTPError as exc:
            raise BridgeError(f"Tech Lead handshake failed ({type(exc).__name__})") from None
        if response.status_code >= 300:
            raise BridgeError(f"Tech Lead handshake was rejected (HTTP {response.status_code})")
        try:
            result = response.json()
        except (ValueError, json.JSONDecodeError):
            raise BridgeError("Tech Lead server returned invalid handshake JSON") from None
        token = result.get("access_token") if isinstance(result, dict) else None
        if not isinstance(token, str) or not token:
            raise BridgeError("Tech Lead handshake returned no session token")
        self._token = token
        return {key: value for key, value in result.items() if key != "access_token"}

    def heartbeat(self) -> dict:
        return self._request("POST", "/api/lead/client/heartbeat", json_body={})

    def handover(self) -> dict:
        return self._request("GET", "/api/lead/client/handover")

    def create_task(self, task: dict) -> dict:
        return self._request("POST", "/api/lead/client/tasks", json_body=task)

    def task_diff(self, task_id: str, offset: int = 0, limit: int = 250_000) -> dict:
        self._validate_task_id(task_id)
        if not isinstance(offset, int) or not isinstance(limit, int) or offset < 0 or not 1 <= limit <= 250_000:
            raise BridgeError("Diff pagination is invalid")
        response = self._request("GET", f"/api/lead/client/tasks/{task_id}/diff?offset={offset}&limit={limit}")
        return response

    def review(self, task_id: str, decision: str, comments: str = "") -> dict:
        self._validate_task_id(task_id)
        if decision not in {"approve", "request_changes", "comment"}:
            raise BridgeError("Review decision is invalid")
        return self._request("POST", f"/api/lead/client/tasks/{task_id}/reviews",
                             json_body={"decision": decision, "comments": comments[:30_000]})

    @staticmethod
    def _validate_task_id(task_id: str) -> None:
        try:
            uuid.UUID(task_id)
        except (AttributeError, TypeError, ValueError):
            raise BridgeError("Task id is invalid") from None


def _print_json(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _interactive(bridge: TechLeadBridge, heartbeat_seconds: int) -> None:
    stopped = threading.Event()
    heartbeat_error: list[str] = []

    def keep_lease() -> None:
        while not stopped.wait(heartbeat_seconds):
            try:
                bridge.heartbeat()
            except BridgeError as exc:
                heartbeat_error.append(str(exc))
                print(f"Heartbeat stopped: {exc}")
                return

    thread = threading.Thread(target=keep_lease, name="tech-lead-heartbeat", daemon=True)
    thread.start()
    print("Paired. Commands: handover, task <json-file>, diff <task-id>, review <id> <decision> [comments], quit")
    try:
        while not heartbeat_error:
            try:
                line = input("tech-lead> ").strip()
            except EOFError:
                break
            if not line:
                continue
            command, _, rest = line.partition(" ")
            if command in {"quit", "exit"}:
                break
            try:
                if command == "handover":
                    _print_json(bridge.handover())
                elif command == "heartbeat":
                    _print_json(bridge.heartbeat())
                elif command == "task":
                    path = Path(rest.strip()).expanduser()
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(payload, dict):
                        raise BridgeError("Task JSON must contain an object")
                    _print_json(bridge.create_task(payload))
                elif command == "diff":
                    _print_json(bridge.task_diff(rest.strip()))
                elif command == "review":
                    task_id, _, remainder = rest.partition(" ")
                    decision, _, comments = remainder.partition(" ")
                    _print_json(bridge.review(task_id, decision, comments))
                else:
                    print("Unknown command. Use handover, task, diff, review, heartbeat, or quit.")
            except (BridgeError, OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"Command failed: {exc}")
    finally:
        stopped.set()
        thread.join(timeout=1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Local Orchestrator URL, for example http://127.0.0.1:43123")
    parser.add_argument("--client-name", default="Local Tech Lead CLI")
    args = parser.parse_args()
    try:
        bridge = TechLeadBridge(args.base_url, args.client_name)
    except BridgeError as exc:
        parser.error(str(exc))
    try:
        pairing_code = getpass.getpass("One-time pairing code (kept only in memory): ")
        session = bridge.pair(pairing_code)
        print(f"Connected: {session['client_name']} | scopes: {', '.join(session['scopes'])}")
        print(f"Session expires: {session['session_expires_at']}; lease renews every 30 seconds.")
        bridge.heartbeat()
        _print_json(bridge.handover())
        _interactive(bridge, int(session.get("heartbeat_interval_seconds", 30)))
    except BridgeError as exc:
        print(f"Tech Lead bridge stopped: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nTech Lead bridge stopped; the control lease expires automatically.")
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
