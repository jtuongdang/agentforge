"""Opt-in, read-only remote viewer pairing with independent credentials."""
from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Request

from . import db

INVITE_TTL_SECONDS = 300
REQUEST_TTL_SECONDS = 300
SESSION_TTL_SECONDS = 12 * 60 * 60
DELIVERY_TTL_SECONDS = 60
MAX_REMOTE_BODY_BYTES = 128 * 1024
_LOCAL_PATH_RE = re.compile(r"(?i)(?:\b[A-Z]:\\(?:[^\\\s]+\\)*[^\\\s]*|/(?:Users|home|private|tmp|var|Volumes|workspace|root)(?:/[^\s]+)*)")
_deliveries: dict[str, tuple[str, str, str, float]] = {}
_delivery_lock = threading.Lock()
_ws_tickets: dict[str, tuple[str, str, float]] = {}
_ws_connections: dict[str, int] = {}
_ws_lock = threading.Lock()
_rest_windows: dict[str, tuple[float, int]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_label(value: str) -> str:
    from .validation import redact_validation_output
    return _LOCAL_PATH_RE.sub("[local path withheld]", redact_validation_output(value))[:240]


def recover_after_restart() -> None:
    """Revoke process-bound browser sessions and pending one-time deliveries after restart."""
    now = _iso(_now())
    with db.transaction() as con:
        active = con.execute("SELECT id FROM remote_devices WHERE state='active'").fetchall()
        pending = con.execute("SELECT id,invite_id FROM remote_pairing_requests WHERE state IN ('pending','approved','delivered')").fetchall()
        con.execute("UPDATE remote_devices SET state='expired',revoked_at=? WHERE state='active'", (now,))
        con.execute("UPDATE remote_pairing_requests SET state='expired' WHERE state IN ('pending','approved','delivered')")
        con.execute("UPDATE remote_invites SET state='revoked' WHERE state='requested'")
    with _delivery_lock:
        _deliveries.clear()
    if active or pending:
        db.event(None, "remote_sessions_recovered", "Remote sessions and pending pairing deliveries expired at application restart.",
                 {"devices_expired": len(active), "pairings_expired": len(pending)})


def remote_boundary_enabled() -> bool:
    """Only enable when the operator has configured an exact HTTPS ingress and proxy."""
    origin = os.getenv("CLINIC_ORCHESTRATOR_REMOTE_ORIGIN", "").strip()
    proxy_ips = os.getenv("CLINIC_ORCHESTRATOR_REMOTE_PROXY_IPS", "").strip()
    if not origin.startswith("https://") or not proxy_ips:
        return False
    try:
        from urllib.parse import urlsplit
        parsed = urlsplit(origin)
        if not parsed.hostname or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return False
        if parsed.username or parsed.password:
            return False
        for value in proxy_ips.split(","):
            ipaddress.ip_address(value.strip())
        return True
    except (ValueError, TypeError):
        return False


def trusted_ingress_peer_allowed(request: Request) -> bool:
    """Verify the configured public host, HTTPS signal, and reverse-proxy peer."""
    if not remote_boundary_enabled():
        return False
    from urllib.parse import urlsplit
    origin = os.environ["CLINIC_ORCHESTRATOR_REMOTE_ORIGIN"].strip().rstrip("/")
    parsed = urlsplit(origin)
    expected_host = parsed.netloc.lower()
    request_host = request.headers.get("host", "").strip().lower()
    # The ingress must overwrite these headers. TLS is required at the ingress boundary.
    if request_host != expected_host:
        return False
    if request.headers.get("x-forwarded-proto", "").lower() != "https":
        return False
    client_ip = request.client.host if request.client else ""
    try:
        peer = ipaddress.ip_address(client_ip)
        trusted = [ipaddress.ip_address(item.strip()) for item in
                   os.environ["CLINIC_ORCHESTRATOR_REMOTE_PROXY_IPS"].split(",")]
    except ValueError:
        return False
    return peer in trusted


def ingress_request_allowed(request: Request) -> bool:
    if not trusted_ingress_peer_allowed(request):
        return False
    origin = os.environ["CLINIC_ORCHESTRATOR_REMOTE_ORIGIN"].strip().rstrip("/")
    request_origin = request.headers.get("origin", "").strip().rstrip("/")
    if request_origin and request_origin != origin:
        return False
    if not request_origin and (request.method != "GET" or request.headers.get("sec-fetch-site") != "same-origin"):
        return False
    return True


def create_invite() -> dict:
    invite_id = str(uuid.uuid4())
    code = secrets.token_urlsafe(32)
    now = _now()
    expires = now + timedelta(seconds=INVITE_TTL_SECONDS)
    with db.transaction() as con:
        active = con.execute("SELECT COUNT(*) FROM remote_invites WHERE state IN ('issued','requested') AND expires_at>?",
                             (_iso(now),)).fetchone()[0]
        if active >= 10:
            raise RuntimeError("Remote invitation capacity reached; wait for an invitation to expire")
        con.execute("""INSERT INTO remote_invites(id,token_hash,state,created_at,expires_at)
            VALUES(?,?,'issued',?,?)""", (invite_id, _hash(code), _iso(now), _iso(expires)))
    db.event(None, "remote_invite_created", "Short-lived remote viewer invitation created.",
             {"invite_id": invite_id, "expires_at": _iso(expires), "scopes": ["summary:read"]})
    return {"invite_id": invite_id, "pairing_code": code, "expires_at": _iso(expires),
            "scopes": ["summary:read"], "transport": "HTTPS ingress over private VPN"}


def request_pairing(code: str, client_id: str, client_name: str) -> dict:
    if not code or len(code) > 256:
        raise PermissionError("Invitation is invalid or expired")
    if not client_id.strip() or len(client_id) > 160 or not client_name.strip() or len(client_name) > 120:
        raise ValueError("Device id and name must be 1–160 and 1–120 characters")
    request_id = str(uuid.uuid4())
    poll_secret = secrets.token_urlsafe(32)
    now = _now()
    expiry = now + timedelta(seconds=REQUEST_TTL_SECONDS)
    with db.transaction() as con:
        con.execute("BEGIN IMMEDIATE")
        invite = con.execute("SELECT * FROM remote_invites WHERE token_hash=?", (_hash(code),)).fetchone()
        if invite is None or invite["state"] != "issued" or invite["expires_at"] <= _iso(now):
            raise PermissionError("Invitation is invalid, expired, or already used")
        con.execute("""INSERT INTO remote_pairing_requests(id,invite_id,poll_hash,client_id,client_name,
            state,created_at,expires_at) VALUES(?,?,?,?,?,'pending',?,?)""",
            (request_id, invite["id"], _hash(poll_secret), client_id.strip(), client_name.strip(), _iso(now), _iso(expiry)))
        con.execute("UPDATE remote_invites SET state='requested' WHERE id=? AND state='issued'", (invite["id"],))
    db.event(None, "remote_pairing_requested", "A remote device requested owner approval.",
             {"request_id": request_id, "client_id": client_id.strip(), "client_name": client_name.strip()})
    return {"request_id": request_id, "poll_secret": poll_secret, "state": "pending",
            "expires_at": _iso(expiry), "message": "Waiting for approval in the desktop app."}


def pending_requests() -> list[dict]:
    now = _iso(_now())
    with db.connect() as con:
        rows = con.execute("""SELECT id,client_id,client_name,created_at,expires_at,state
            FROM remote_pairing_requests WHERE state='pending' AND expires_at>? ORDER BY created_at""", (now,)).fetchall()
    return [dict(row) for row in rows]


def approve_request(request_id: str) -> dict:
    device_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    now = _now()
    expiry = now + timedelta(seconds=SESSION_TTL_SECONDS)
    with _delivery_lock:
        with db.transaction() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM remote_pairing_requests WHERE id=?", (request_id,)).fetchone()
            if row is None or row["state"] != "pending" or row["expires_at"] <= _iso(now):
                raise LookupError("Pending remote pairing request not found or expired")
            con.execute("""INSERT INTO remote_devices(id,client_id,client_name,token_hash,state,created_at,last_seen,expires_at)
                VALUES(?,?,?,?,'active',?,?,?)""",
                (device_id, row["client_id"], row["client_name"], _hash(token), _iso(now), _iso(now), _iso(expiry)))
            con.execute("UPDATE remote_pairing_requests SET state='approved',device_id=? WHERE id=?",
                        (device_id, request_id))
            con.execute("UPDATE remote_invites SET state='consumed' WHERE id=?", (row["invite_id"],))
        _deliveries[request_id] = (_hash(row["poll_hash"]), device_id, token,
                                   time.monotonic() + DELIVERY_TTL_SECONDS)
    db.event(None, "remote_device_paired", "Owner approved a read-only remote device.",
             {"device_id": device_id, "client_id": row["client_id"], "client_name": row["client_name"]})
    return {"device_id": device_id, "client_id": row["client_id"], "client_name": row["client_name"],
            "scopes": ["summary:read"], "expires_at": _iso(expiry)}


def deny_request(request_id: str) -> bool:
    now = _iso(_now())
    with db.transaction() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT invite_id,state FROM remote_pairing_requests WHERE id=?", (request_id,)).fetchone()
        if row is None or row["state"] != "pending":
            return False
        con.execute("UPDATE remote_pairing_requests SET state='denied' WHERE id=?", (request_id,))
        con.execute("UPDATE remote_invites SET state='revoked' WHERE id=?", (row["invite_id"],))
    db.event(None, "remote_pairing_denied", "Owner denied a remote pairing request.", {"request_id": request_id})
    return True


def poll_pairing(request_id: str, poll_secret: str) -> dict:
    if not poll_secret or len(poll_secret) > 256:
        raise PermissionError("Pairing request is invalid")
    with _delivery_lock:
        delivery = _deliveries.get(request_id)
        with db.transaction() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM remote_pairing_requests WHERE id=?", (request_id,)).fetchone()
            if row is None or not secrets.compare_digest(row["poll_hash"], _hash(poll_secret)):
                raise PermissionError("Pairing request is invalid")
            if row["expires_at"] <= _iso(_now()) and row["state"] in {"pending", "approved"}:
                con.execute("UPDATE remote_pairing_requests SET state='expired' WHERE id=?", (request_id,))
                return {"state": "expired"}
            if row["state"] == "pending":
                return {"state": "pending"}
            if row["state"] == "denied":
                return {"state": "denied"}
            if row["state"] == "approved" and (delivery is None or delivery[3] <= time.monotonic()):
                con.execute("UPDATE remote_pairing_requests SET state='expired' WHERE id=?", (request_id,))
                con.execute("UPDATE remote_devices SET state='expired',revoked_at=? WHERE id=? AND state='active'",
                            (_iso(_now()), row["device_id"]))
                _deliveries.pop(request_id, None)
                return {"state": "expired"}
            if row["state"] != "approved" or delivery is None:
                return {"state": "expired" if row["state"] in {"expired", "delivered"} else row["state"]}
            if not secrets.compare_digest(delivery[0], _hash(row["poll_hash"])):
                raise PermissionError("Pairing request is invalid")
            con.execute("UPDATE remote_pairing_requests SET state='delivered' WHERE id=? AND state='approved'", (request_id,))
            device = con.execute("SELECT expires_at,state FROM remote_devices WHERE id=?", (delivery[1],)).fetchone()
            _deliveries.pop(request_id, None)
            if device is None or device["state"] != "active":
                return {"state": "expired"}
            return {"state": "approved", "device_id": delivery[1], "access_token": delivery[2],
                    "expires_at": device["expires_at"], "scopes": ["summary:read"]}


def cancel_pairing_request(request_id: str, poll_secret: str) -> bool:
    now = _iso(_now())
    with _delivery_lock:
        with db.transaction() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT invite_id,poll_hash,state,device_id FROM remote_pairing_requests WHERE id=?",
                              (request_id,)).fetchone()
            if row is None or row["state"] not in {"pending", "approved"} or not secrets.compare_digest(row["poll_hash"], _hash(poll_secret)):
                return False
            con.execute("UPDATE remote_pairing_requests SET state='denied' WHERE id=?", (request_id,))
            con.execute("UPDATE remote_invites SET state='revoked' WHERE id=?", (row["invite_id"],))
            if row["state"] == "approved" and row["device_id"]:
                con.execute("UPDATE remote_devices SET state='revoked',revoked_at=? WHERE id=? AND state='active'",
                            (now, row["device_id"]))
        _deliveries.pop(request_id, None)
    db.event(None, "remote_pairing_cancelled", "A remote device cancelled its pending pairing request.",
             {"request_id": request_id})
    return True


def authorize(token: str) -> dict | None:
    if not token or len(token) > 256:
        return None
    now = _iso(_now())
    with db.transaction() as con:
        row = con.execute("""SELECT id,client_id,client_name,expires_at FROM remote_devices
            WHERE token_hash=? AND state='active' AND expires_at>?""", (_hash(token), now)).fetchone()
        if row is None:
            return None
        con.execute("UPDATE remote_devices SET last_seen=? WHERE id=?", (now, row["id"]))
    return dict(row)


def device_is_active(device_id: str) -> bool:
    with db.connect() as con:
        row = con.execute("SELECT 1 FROM remote_devices WHERE id=? AND state='active' AND expires_at>?",
                          (device_id, _iso(_now()))).fetchone()
    return row is not None


def issue_ws_ticket(device_id: str, task_id: str) -> str:
    if not device_is_active(device_id):
        raise PermissionError("Remote device session expired or revoked")
    with db.connect() as con:
        if con.execute("""SELECT 1 FROM tasks WHERE id=? AND id IN (
            SELECT id FROM tasks ORDER BY updated_at DESC,id LIMIT 50)""", (task_id,)).fetchone() is None:
            raise LookupError("Task not found")
    raw = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _ws_lock:
        for key, value in list(_ws_tickets.items()):
            if value[2] <= now:
                _ws_tickets.pop(key, None)
        if len(_ws_tickets) >= 128:
            raise RuntimeError("Remote event ticket capacity reached; retry shortly")
        _ws_tickets[_hash(raw)] = (device_id, task_id, now + 30)
    return raw


def consume_ws_ticket(raw: str, task_id: str) -> str | None:
    with _ws_lock:
        ticket = _ws_tickets.pop(_hash(raw), None) if raw else None
        if ticket is None or ticket[2] <= time.monotonic() or ticket[1] != task_id:
            return None
        return ticket[0]


def open_ws(device_id: str) -> bool:
    with _ws_lock:
        total = sum(_ws_connections.values())
        current = _ws_connections.get(device_id, 0)
        if total >= 32 or current >= 2:
            return False
        _ws_connections[device_id] = current + 1
        return True


def close_ws(device_id: str) -> None:
    with _ws_lock:
        current = _ws_connections.get(device_id, 0)
        if current <= 1:
            _ws_connections.pop(device_id, None)
        else:
            _ws_connections[device_id] = current - 1


def consume_rest_budget(device_id: str, limit: int = 120, period_seconds: int = 60) -> bool:
    now = time.monotonic()
    with _ws_lock:
        start, count = _rest_windows.get(device_id, (now, 0))
        if now - start >= period_seconds:
            start, count = now, 0
        if count >= limit:
            return False
        _rest_windows[device_id] = (start, count + 1)
        if len(_rest_windows) > 1024:
            for key, value in list(_rest_windows.items()):
                if now - value[0] >= period_seconds:
                    _rest_windows.pop(key, None)
        return True


def revoke_owner_access() -> None:
    now = _iso(_now())
    with db.transaction() as con:
        con.execute("UPDATE remote_devices SET state='revoked',revoked_at=? WHERE state='active'", (now,))
        con.execute("UPDATE remote_task_diff_grants SET revoked_at=? WHERE revoked_at IS NULL", (now,))
        con.execute("UPDATE remote_invites SET state='revoked' WHERE state IN ('issued','requested')")
        con.execute("UPDATE remote_pairing_requests SET state='denied' WHERE state IN ('pending','approved','delivered')")
    with _delivery_lock:
        _deliveries.clear()
    with _ws_lock:
        _ws_tickets.clear()


def list_devices() -> list[dict]:
    with db.connect() as con:
        rows = con.execute("""SELECT id,client_id,client_name,state,created_at,last_seen,expires_at
            FROM remote_devices ORDER BY created_at DESC""").fetchall()
        devices = []
        for row in rows:
            item = dict(row)
            item["diff_grants"] = [dict(grant) for grant in con.execute("""SELECT task_id,revision_sha,granted_at
                FROM remote_task_diff_grants WHERE device_id=? AND revoked_at IS NULL ORDER BY granted_at DESC""",
                (row["id"],)).fetchall()]
            devices.append(item)
    return devices


def revoke_device(device_id: str) -> bool:
    now = _iso(_now())
    with db.transaction() as con:
        con.execute("BEGIN IMMEDIATE")
        changed = con.execute("""UPDATE remote_devices SET state='revoked',revoked_at=?
            WHERE id=? AND state='active'""", (now, device_id)).rowcount
        con.execute("UPDATE remote_task_diff_grants SET revoked_at=? WHERE device_id=? AND revoked_at IS NULL",
                    (now, device_id))
    if changed:
        db.event(None, "remote_device_revoked", "Owner revoked a remote device.", {"device_id": device_id})
    return changed == 1


def grant_full_diff(device_id: str, task_id: str, revision_sha: str) -> dict:
    now = _iso(_now())
    if len(revision_sha) not in {40, 64} or any(c not in "0123456789abcdef" for c in revision_sha.lower()):
        raise ValueError("A full diff grant must pin a valid Git revision")
    with db.transaction() as con:
        device = con.execute("SELECT id FROM remote_devices WHERE id=? AND state='active' AND expires_at>?",
                             (device_id, now)).fetchone()
        task = con.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone()
        if device is None or task is None:
            raise LookupError("Active device or task not found")
        con.execute("""INSERT INTO remote_task_diff_grants(device_id,task_id,revision_sha,granted_at,revoked_at)
            VALUES(?,?,?,?,NULL) ON CONFLICT(device_id,task_id) DO UPDATE SET
            revision_sha=excluded.revision_sha,granted_at=excluded.granted_at,revoked_at=NULL""",
            (device_id, task_id, revision_sha.lower(), now))
    db.event(task_id, "remote_diff_granted", "Owner granted this device full diff access for a pinned task revision.",
             {"device_id": device_id, "revision_sha": revision_sha.lower()})
    return {"device_id": device_id, "task_id": task_id, "revision_sha": revision_sha.lower(), "granted_at": now}


def diff_grant(device_id: str, task_id: str, current_sha: str) -> bool:
    with db.connect() as con:
        row = con.execute("""SELECT g.revision_sha FROM remote_task_diff_grants g
            JOIN remote_devices d ON d.id=g.device_id
            WHERE g.device_id=? AND g.task_id=? AND g.revoked_at IS NULL AND d.state='active' AND d.expires_at>?""",
            (device_id, task_id, _iso(_now()))).fetchone()
    return bool(row and secrets.compare_digest(row["revision_sha"], current_sha.lower()))


def revoke_diff_grant(device_id: str, task_id: str) -> bool:
    now = _iso(_now())
    with db.transaction() as con:
        changed = con.execute("""UPDATE remote_task_diff_grants SET revoked_at=?
            WHERE device_id=? AND task_id=? AND revoked_at IS NULL""", (now, device_id, task_id)).rowcount
    if changed:
        db.event(task_id, "remote_diff_revoked", "Owner revoked task-specific full diff access.",
                 {"device_id": device_id})
    return changed == 1
