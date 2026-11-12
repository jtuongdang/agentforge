from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db


CONTRACT_VERSION = 1
PAIRING_TTL_SECONDS = 300
LEASE_TTL_SECONDS = 90
SESSION_TTL_SECONDS = 8 * 60 * 60
ALLOWED_SCOPES = {"handover:read", "task:create", "task:review"}
_pairings: dict[str, dict[str, Any]] = {}


def _utc_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_pairing(scopes: list[str]) -> dict:
    requested = sorted(set(scopes))
    if not requested or not set(requested).issubset(ALLOWED_SCOPES):
        raise ValueError("Pairing scopes must be selected from the supported least-privilege set")
    if status()["connected"]:
        raise RuntimeError("A Tech Lead already owns the control lease; revoke it before creating another pairing")
    now = time.monotonic()
    for key, pairing in list(_pairings.items()):
        if pairing["expires_monotonic"] <= now:
            _pairings.pop(key, None)
    _pairings.clear()
    code = secrets.token_urlsafe(24)
    key = _hash(code)
    expires_at = _utc_after(PAIRING_TTL_SECONDS)
    _pairings[key] = {"scopes": requested, "expires_monotonic": now + PAIRING_TTL_SECONDS,
                      "expires_at": expires_at}
    return {"pairing_code": code, "expires_at": expires_at, "contract_version": CONTRACT_VERSION,
            "scopes": requested}


def handshake(pairing_code: str, client_id: str, client_name: str,
              requested_scopes: list[str] | None = None) -> dict:
    if not client_id.strip() or len(client_id) > 160 or not client_name.strip() or len(client_name) > 160:
        raise ValueError("Client id and name must be 1–160 characters")
    key = _hash(pairing_code)
    pairing = _pairings.get(key)
    if pairing is None or pairing["expires_monotonic"] <= time.monotonic():
        _pairings.pop(key, None)
        raise PermissionError("Pairing code is invalid or expired")
    scopes = sorted(set(requested_scopes if requested_scopes is not None else pairing["scopes"]))
    if not scopes or not set(scopes).issubset(pairing["scopes"]):
        raise PermissionError("Requested scopes exceed the owner's pairing grant")

    session_id = str(uuid.uuid4())
    access_token = secrets.token_urlsafe(32)
    now = _now()
    session_expires = _utc_after(SESSION_TTL_SECONDS)
    lease_expires = _utc_after(LEASE_TTL_SECONDS)
    with db.transaction() as con:
        con.execute("BEGIN IMMEDIATE")
        lease = con.execute("SELECT session_id,expires_at FROM lead_lease WHERE id=1").fetchone()
        if lease and lease["expires_at"] > now:
            raise RuntimeError("Another Tech Lead currently owns the control lease")
        if lease:
            con.execute("UPDATE lead_sessions SET state='expired' WHERE id=? AND state='active'", (lease["session_id"],))
            con.execute("DELETE FROM lead_lease WHERE id=1")
        con.execute("""INSERT INTO lead_sessions(id,client_id,client_name,token_hash,scopes,state,created_at,last_seen,expires_at)
            VALUES(?,?,?,?,?,'active',?,?,?)""",
            (session_id, client_id.strip(), client_name.strip(), _hash(access_token), db.dumps(scopes), now, now, session_expires))
        con.execute("INSERT INTO lead_lease(id,session_id,expires_at,updated_at) VALUES(1,?,?,?)",
                    (session_id, lease_expires, now))
    _pairings.pop(key, None)
    db.event(None, "tech_lead_connected", f"Tech Lead connected: {client_name.strip()}.",
             {"session_id": session_id, "client_id": client_id.strip(), "scopes": scopes})
    return {"session_id": session_id, "access_token": access_token, "client_id": client_id.strip(),
            "client_name": client_name.strip(), "scopes": scopes, "lease_expires_at": lease_expires,
            "session_expires_at": session_expires, "heartbeat_interval_seconds": 30}


def session_for_token(access_token: str) -> dict | None:
    if not access_token:
        return None
    now = _now()
    with db.connect() as con:
        row = con.execute("""SELECT s.* FROM lead_sessions s JOIN lead_lease l ON l.session_id=s.id
            WHERE s.token_hash=? AND s.state='active' AND s.expires_at>? AND l.expires_at>?""",
                          (_hash(access_token), now, now)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["scopes"] = db.json.loads(result["scopes"])
    return result


def authorize(access_token: str, scope: str | None = None) -> dict | None:
    session = session_for_token(access_token)
    if session is None or (scope is not None and scope not in session["scopes"]):
        return None
    return session


def heartbeat(access_token: str) -> dict | None:
    session = session_for_token(access_token)
    if session is None:
        return None
    now = _now()
    lease_expires = _utc_after(LEASE_TTL_SECONDS)
    if lease_expires > session["expires_at"]:
        lease_expires = session["expires_at"]
    with db.transaction() as con:
        con.execute("BEGIN IMMEDIATE")
        lease = con.execute("SELECT session_id,expires_at FROM lead_lease WHERE id=1").fetchone()
        if not lease or lease["session_id"] != session["id"] or lease["expires_at"] <= now:
            return None
        con.execute("UPDATE lead_sessions SET last_seen=? WHERE id=? AND state='active'", (now, session["id"]))
        con.execute("UPDATE lead_lease SET expires_at=?,updated_at=? WHERE id=1 AND session_id=?",
                    (lease_expires, now, session["id"]))
    return {"session_id": session["id"], "lease_expires_at": lease_expires,
            "session_expires_at": session["expires_at"], "scopes": session["scopes"]}


def revoke(session_id: str) -> bool:
    now = _now()
    with db.transaction() as con:
        con.execute("BEGIN IMMEDIATE")
        changed = con.execute("UPDATE lead_sessions SET state='revoked',revoked_at=? WHERE id=? AND state='active'",
                              (now, session_id)).rowcount
        con.execute("DELETE FROM lead_lease WHERE id=1 AND session_id=?", (session_id,))
    if changed:
        _pairings.clear()
        db.event(None, "tech_lead_revoked", "Tech Lead control lease was revoked.", {"session_id": session_id})
    return changed == 1


def revoke_owner_access() -> None:
    with db.transaction() as con:
        con.execute("UPDATE lead_sessions SET state='revoked',revoked_at=? WHERE state='active'", (_now(),))
        con.execute("DELETE FROM lead_lease")
    _pairings.clear()


def status() -> dict:
    now = _now()
    with db.connect() as con:
        lease = con.execute("""SELECT l.session_id,l.expires_at lease_expires_at,s.client_id,s.client_name,
                s.scopes,s.state,s.last_seen,s.expires_at session_expires_at
            FROM lead_lease l JOIN lead_sessions s ON s.id=l.session_id WHERE l.id=1""").fetchone()
    connected = bool(lease and lease["state"] == "active" and lease["lease_expires_at"] > now
                     and lease["session_expires_at"] > now)
    if not connected:
        return {"connected": False, "session": None}
    item = dict(lease)
    item["scopes"] = db.json.loads(item["scopes"])
    item.pop("state", None)
    return {"connected": True, "session": item}


def record_review(task_id: str, session_id: str, decision: str, comments: str) -> dict:
    review_id = str(uuid.uuid4())
    with db.transaction() as con:
        con.execute("INSERT INTO lead_reviews(id,task_id,session_id,decision,comments,created_at) VALUES(?,?,?,?,?,?)",
                    (review_id, task_id, session_id, decision, comments, _now()))
    db.event(task_id, "tech_lead_review", f"Tech Lead review recorded: {decision}.",
             {"review_id": review_id, "session_id": session_id, "decision": decision})
    return {"id": review_id, "task_id": task_id, "session_id": session_id,
            "decision": decision, "comments": comments}


def reviews(task_id: str) -> list[dict]:
    with db.connect() as con:
        return [dict(row) for row in con.execute("""SELECT id,task_id,session_id,decision,comments,created_at
            FROM lead_reviews WHERE task_id=? ORDER BY created_at,id""", (task_id,)).fetchall()]
