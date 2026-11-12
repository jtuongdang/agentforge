from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import db
from . import portability
from . import providers
from . import tech_lead
from . import remote_pairing
from .engine import get_task, log_event, set_task
from . import fleet as fleet_manager
from .git_ops import (changed_paths, check_repository_sync, is_github_remote, list_repository_branches,
                      git_author, register_repository, repository, run, validate_repository_base,
                      validated_revision_is_current)
from .models import (AgentCreate, CompleteHandoff, ContractCreate, FleetSettingsUpdate, LeadHandshake,
                     LeadPairingCreate, LeadReview, ProjectCheckpointCreate, ProjectCheckpointRestore,
                     ProviderConnectionCreate, ProviderConnectionUpdate,
                     RemoteDiffGrant, RemotePairingPoll, RemotePairingRequest, RemoteWsTicketRequest,
                     RepositoryCreate, RepositoryPreflight, TaskCreate)
from .contracts import ContractManager
from .orchestrator import Orchestrator
from .settings import settings
from .storage import prune_events, storage_snapshot

orchestrator = Orchestrator()
registry = orchestrator.registry
worktrees = orchestrator.worktrees
validation_engine = orchestrator.validation
contracts_manager = ContractManager()
pr_manager = orchestrator.github
reporting = orchestrator.reporting
execution_engine = orchestrator.execution
scheduler = orchestrator.scheduler
ALLOWED_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173", "http://[::1]:5173", "tauri://localhost",
                   "http://tauri.localhost", "https://tauri.localhost"}
if remote_pairing.remote_boundary_enabled():
    ALLOWED_ORIGINS.add(os.environ["CLINIC_ORCHESTRATOR_REMOTE_ORIGIN"].strip().rstrip("/"))
_ws_tickets: dict[str, float] = {}


def _api_token_path() -> Path:
    return settings.api_token_file


def _local_api_token() -> str:
    configured = os.getenv("CLINIC_ORCHESTRATOR_API_TOKEN")
    if configured:
        return configured
    path = _api_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        token = path.read_text(encoding="utf-8").strip()
        if token:
            if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                path.chmod(0o600)
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip()
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(token)
    return token


def _host_is_loopback(host: str) -> bool:
    value = host.strip().lower()
    if value.startswith("[") and "]" in value:
        hostname = value[1:value.index("]")]
    elif value.count(":") > 1:
        hostname = value
    else:
        hostname = value.split(":", 1)[0]
    return hostname in {"127.0.0.1", "localhost", "::1", "testserver"}


def bootstrap() -> None:
    db.init_db()
    remote_pairing.recover_after_restart()
    registry.refresh()
    reconcile_interrupted_tasks()


def reconcile_interrupted_tasks() -> None:
    interrupted = ("planning", "assigned", "running", "validating", "recovering", "publishing")
    with db.connect() as con:
        marks = ",".join("?" for _ in interrupted)
        rows = con.execute(f"""SELECT t.id,t.status,t.assigned_agent_id,t.worktree,t.base_sha,t.base_branch,
            t.file_globs,a.file_globs AS agent_file_globs FROM tasks t LEFT JOIN agents a ON a.id=t.assigned_agent_id
            WHERE t.status IN ({marks})""", interrupted).fetchall()
    for task in rows:
        checkpoint = {"retained_worktree": task["worktree"], "scanned": False,
                      "requires_manual_inspection": True}
        scan_problem = None
        if task["worktree"] and Path(task["worktree"]).is_dir():
            try:
                paths = changed_paths(task["worktree"], task["base_sha"] or task["base_branch"])
                task_globs = json.loads(task["file_globs"] or "[]")
                agent_globs = json.loads(task["agent_file_globs"] or '["**/*"]')
                outside = validation_engine.inspect_scope(paths, task_globs, agent_globs)
                secret_files = validation_engine.inspect_secrets(task["worktree"], paths)
                checkpoint = {"retained_worktree": task["worktree"], "base_ref": task["base_sha"] or task["base_branch"],
                              "changed_paths": paths, "scanned": True,
                              "outside_scope": outside, "possible_secret_files": secret_files,
                              "requires_manual_inspection": bool(outside or secret_files)}
            except Exception as exc:
                scan_problem = type(exc).__name__
                checkpoint["scan_error"] = scan_problem
        with db.transaction() as con:
            publishing = task["status"] == "publishing"
            blocked_scan = bool(checkpoint.get("outside_scope") or checkpoint.get("possible_secret_files") or scan_problem)
            status = "blocked" if publishing or blocked_scan else "failed"
            if publishing:
                message = ("Orchestrator stopped during publication. Check the remote branch and GitHub before retrying; "
                           "no automatic push or PR retry was performed.")
            elif blocked_scan:
                message = "Restart checkpoint scan found unsafe or unreadable worktree state. Inspect the retained worktree before any retry."
            else:
                message = "Orchestrator restarted while this task was active. It was not resumed; inspect task details and the retained checkpoint."
            con.execute("UPDATE tasks SET status=?,error=?,finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (status, message, task["id"]))
            con.execute("UPDATE runs SET state='interrupted',exit_code=125,finished_at=CURRENT_TIMESTAMP,report=? "
                        "WHERE task_id=? AND state='running'", (db.dumps({"error": message,
                            "failure_class": "restart_interrupted", "checkpoint": checkpoint}), task["id"]))
            con.execute("UPDATE task_writer_leases SET state='orphaned',updated_at=CURRENT_TIMESTAMP "
                        "WHERE task_id=? AND state='active'", (task["id"],))
            con.execute("INSERT INTO events(task_id,agent_id,kind,message,details) VALUES(?,?,?,?,?)",
                        (task["id"], task["assigned_agent_id"], "restart_recovery", message, db.dumps({
                            "previous_status": task["status"], "retained_worktree": task["worktree"],
                            "writer_lease": "orphaned"})))


@asynccontextmanager
async def lifespan(_: FastAPI):
    bootstrap()
    worker = asyncio.create_task(orchestrator.start())
    yield
    await orchestrator.stop()
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)


app = FastAPI(title="DevAgent Orchestrator", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=sorted(ALLOWED_ORIGINS),
                   allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
                   allow_headers=["Content-Type", "Authorization"])


@app.middleware("http")
async def require_local_api_token(request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    if request.url.path.startswith("/remote/api/"):
        from fastapi.responses import JSONResponse
        if not remote_pairing.ingress_request_allowed(request):
            return JSONResponse({"detail": "Remote HTTPS ingress is not configured or request boundary is invalid"}, status_code=403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            try:
                body_size = int(request.headers.get("content-length", "-1"))
            except ValueError:
                body_size = -1
            if body_size < 0 or body_size > remote_pairing.MAX_REMOTE_BODY_BYTES:
                return JSONResponse({"detail": "Remote request body is missing a valid bounded Content-Length"}, status_code=413)
    if request.url.path.startswith("/api/") and request.url.path not in {"/api/session"}:
        if not _host_is_loopback(request.headers.get("host", "")):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Local API host is not allowed"}, status_code=403)
        supplied = request.headers.get("authorization", "")
        if request.url.path == "/api/lead/handshake":
            return await call_next(request)
        if request.url.path.startswith("/api/lead/client/"):
            session = tech_lead.authorize(supplied.removeprefix("Bearer "))
            if session is None:
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "Active Tech Lead session required"}, status_code=401)
            return await call_next(request)
        expected = f"Bearer {_local_api_token()}"
        if not hmac.compare_digest(supplied, expected):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Local API token required"}, status_code=401)
    return await call_next(request)


@app.get("/api/session")
def local_session(request: Request):
    origin = request.headers.get("origin", "")
    if origin not in ALLOWED_ORIGINS or not _host_is_loopback(request.headers.get("host", "")):
        raise HTTPException(403, "Session token is available only to the local application")
    return {"access_token": _local_api_token()}


@app.get("/api/ws-ticket")
def websocket_ticket():
    now = time.monotonic()
    for ticket, expiry in list(_ws_tickets.items()):
        if expiry <= now:
            _ws_tickets.pop(ticket, None)
    ticket = secrets.token_urlsafe(24)
    _ws_tickets[ticket] = now + 30
    return {"ticket": ticket}


def _lead_client_session(request: Request, scope: str | None = None) -> dict:
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    session = tech_lead.authorize(supplied, scope)
    if session is None:
        raise HTTPException(403, "Tech Lead session does not hold the required scope or lease")
    return session


def _remote_device(request: Request) -> dict:
    supplied = request.headers.get("authorization", "")
    if not supplied.startswith("Bearer "):
        raise HTTPException(401, "Remote device session required")
    device = remote_pairing.authorize(supplied.removeprefix("Bearer "))
    if device is None:
        raise HTTPException(401, "Remote device session expired or revoked")
    if not remote_pairing.consume_rest_budget(device["id"]):
        raise HTTPException(429, "Remote device request limit reached; wait one minute and retry")
    return device


@app.post("/api/remote/invites", status_code=201)
def create_remote_invite():
    if not remote_pairing.remote_boundary_enabled():
        raise HTTPException(409, "Configure an HTTPS VPN ingress and trusted proxy before creating remote invitations")
    return remote_pairing.create_invite()


@app.get("/api/remote/status")
def remote_pairing_status():
    return {"enabled": remote_pairing.remote_boundary_enabled(),
            "origin": os.getenv("CLINIC_ORCHESTRATOR_REMOTE_ORIGIN", "").strip().rstrip("/") or None,
            "gateway": (f"127.0.0.1:{os.getenv('CLINIC_ORCHESTRATOR_REMOTE_GATEWAY_PORT', '8766')}"
                        if remote_pairing.remote_boundary_enabled() else None),
            "transport": "VPN + HTTPS ingress",
            "devices": remote_pairing.list_devices(), "pending_requests": remote_pairing.pending_requests()}


@app.get("/api/remote/pairings")
def remote_pairing_requests():
    return remote_pairing.pending_requests()


@app.post("/api/remote/pairings/{request_id}/approve")
def approve_remote_pairing(request_id: str):
    try:
        return remote_pairing.approve_request(request_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/remote/pairings/{request_id}/deny")
def deny_remote_pairing(request_id: str):
    if not remote_pairing.deny_request(request_id):
        raise HTTPException(404, "Pending remote pairing request not found")
    return {"id": request_id, "state": "denied"}


@app.get("/api/remote/devices")
def remote_devices():
    return remote_pairing.list_devices()


@app.post("/api/remote/devices/{device_id}/revoke")
def revoke_remote_device(device_id: str):
    if not remote_pairing.revoke_device(device_id):
        raise HTTPException(404, "Active remote device not found")
    return {"device_id": device_id, "state": "revoked"}


@app.post("/api/remote/devices/{device_id}/tasks/{task_id}/diff-grant")
def grant_remote_task_diff(device_id: str, task_id: str, body: RemoteDiffGrant):
    task = task_row(task_id)
    validated_sha = str(task.get("validation", {}).get("commit") or "").lower()
    expected = f"GRANT FULL DIFF {task_id[:8]} {validated_sha[:12]}"
    if not validated_sha or body.revision_sha.lower() != validated_sha:
        raise HTTPException(409, "Grant revision must exactly match the task's current validated revision")
    if not hmac.compare_digest(body.confirmation, expected):
        raise HTTPException(422, f"To grant, enter: {expected}")
    try:
        return remote_pairing.grant_full_diff(device_id, task_id, validated_sha)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.delete("/api/remote/devices/{device_id}/tasks/{task_id}/diff-grant")
def revoke_remote_task_diff(device_id: str, task_id: str):
    if not remote_pairing.revoke_diff_grant(device_id, task_id):
        raise HTTPException(404, "Active task diff grant not found")
    return {"device_id": device_id, "task_id": task_id, "state": "revoked"}


def _require_remote_ingress(request: Request) -> None:
    if not remote_pairing.ingress_request_allowed(request):
        raise HTTPException(403, "Remote HTTPS ingress is not configured or request boundary is invalid")


@app.post("/remote/api/pairings", status_code=202)
def remote_request_pairing(body: RemotePairingRequest, request: Request):
    _require_remote_ingress(request)
    try:
        return remote_pairing.request_pairing(body.pairing_code, body.client_id, body.client_name)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/remote/api/pairings/{request_id}/poll")
def remote_poll_pairing(request_id: str, body: RemotePairingPoll, request: Request):
    _require_remote_ingress(request)
    try:
        return remote_pairing.poll_pairing(request_id, body.poll_secret)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@app.post("/remote/api/pairings/{request_id}/cancel")
def remote_cancel_pairing(request_id: str, body: RemotePairingPoll, request: Request):
    _require_remote_ingress(request)
    if not remote_pairing.cancel_pairing_request(request_id, body.poll_secret):
        raise HTTPException(404, "Pending pairing request not found")
    return {"request_id": request_id, "state": "cancelled"}


@app.get("/remote/api/tasks")
def remote_task_summaries(request: Request):
    device = _remote_device(request)
    with db.connect() as con:
        rows = con.execute("""SELECT t.id,t.title,t.status,t.priority,t.created_at,t.updated_at,
                r.name AS repository_name,t.validation
            FROM tasks t JOIN repositories r ON r.id=t.repository_id
            ORDER BY t.updated_at DESC,t.id LIMIT 50""").fetchall()
    result = []
    for row in rows:
        try:
            validation = json.loads(row["validation"] or "{}")
        except (TypeError, json.JSONDecodeError):
            validation = {}
        revision = str(validation.get("commit") or "").lower()
        result.append({"id": row["id"], "title": remote_pairing.safe_label(row["title"]), "status": row["status"],
            "priority": row["priority"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "repository_name": remote_pairing.safe_label(row["repository_name"]), "validation_passed": bool(validation.get("passed")),
            "full_diff_available": bool(revision and remote_pairing.diff_grant(device["id"], row["id"], revision))})
    return {"device": {"id": device["id"], "name": device["client_name"], "expires_at": device["expires_at"]},
            "tasks": result, "read_only": True}


@app.get("/remote/api/tasks/{task_id}/diff")
def remote_task_diff(task_id: str, request: Request, offset: int = Query(default=0, ge=0, le=1_000_000),
                     limit: int = Query(default=24000, ge=1, le=24000)):
    device = _remote_device(request)
    task = task_row(task_id)
    revision = str(task.get("validation", {}).get("commit") or "").lower()
    if not revision or not remote_pairing.diff_grant(device["id"], task_id, revision):
        raise HTTPException(403, "This device does not have a current task-specific full diff grant")
    preview = task_diff_preview(task_id, offset, limit)
    return {"task_id": task_id, "revision_sha": revision, "diff": preview.get("diff", ""),
            "offset": offset, "limit": limit, "next_offset": preview.get("next_offset"),
            "truncated": bool(preview.get("truncated", False))}


@app.post("/remote/api/ws-ticket", status_code=204)
def remote_ws_ticket(body: RemoteWsTicketRequest, request: Request, response: Response):
    device = _remote_device(request)
    try:
        ticket = remote_pairing.issue_ws_ticket(device["id"], body.task_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (PermissionError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    response.set_cookie("__Host-devagent-remote-ws", ticket, max_age=30, secure=True,
                        httponly=True, samesite="strict", path="/")


@app.websocket("/remote/ws/tasks/{task_id}/events")
async def remote_stream_events(socket: WebSocket, task_id: str):
    if not remote_pairing.ingress_request_allowed(socket):
        await socket.close(code=4403)
        return
    device_id = remote_pairing.consume_ws_ticket(socket.cookies.get("__Host-devagent-remote-ws", ""), task_id)
    if device_id is None or not remote_pairing.device_is_active(device_id):
        await socket.close(code=4401)
        return
    if not remote_pairing.open_ws(device_id):
        await socket.close(code=4429)
        return
    try:
        task = task_row(task_id)
        await socket.accept()
        last_updated = ""
        while True:
            if not remote_pairing.device_is_active(device_id):
                await socket.close(code=4401)
                return
            current = task_row(task_id)
            if current["updated_at"] != last_updated:
                await socket.send_json({"task_id": task_id, "status": current["status"],
                                        "updated_at": current["updated_at"]})
                last_updated = current["updated_at"]
            await asyncio.sleep(2)
    except HTTPException:
        await socket.close(code=4404)
    except WebSocketDisconnect:
        return
    finally:
        remote_pairing.close_ws(device_id)


@app.get("/api/lead/contract")
def lead_contract():
    return {"version": tech_lead.CONTRACT_VERSION, "pairing_ttl_seconds": tech_lead.PAIRING_TTL_SECONDS,
            "lease_ttl_seconds": tech_lead.LEASE_TTL_SECONDS, "session_ttl_seconds": tech_lead.SESSION_TTL_SECONDS,
            "scopes": sorted(tech_lead.ALLOWED_SCOPES),
            "client_endpoints": {"handshake": "POST /api/lead/handshake", "heartbeat": "POST /api/lead/client/heartbeat",
                                 "handover": "GET /api/lead/client/handover", "create_task": "POST /api/lead/client/tasks",
                                 "task_diff": "GET /api/lead/client/tasks/{task_id}/diff",
                                 "review": "POST /api/lead/client/tasks/{task_id}/reviews"},
            "publication": "Not available to Tech Lead sessions; local operator approval is required."}


@app.get("/api/lead/status")
def lead_status():
    return tech_lead.status()


@app.post("/api/lead/pairing", status_code=201)
def create_lead_pairing(body: LeadPairingCreate):
    try:
        result = tech_lead.create_pairing(body.scopes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.event(None, "tech_lead_pairing_created", "Short-lived Tech Lead pairing code created.",
             {"scopes": result["scopes"], "expires_at": result["expires_at"]})
    return result


@app.post("/api/lead/handshake", status_code=201)
def lead_handshake(body: LeadHandshake):
    try:
        return tech_lead.handshake(body.pairing_code, body.client_id, body.client_name, body.requested_scopes)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/lead/sessions/{session_id}/revoke")
def revoke_lead_session(session_id: str):
    if not tech_lead.revoke(session_id):
        raise HTTPException(404, "Active Tech Lead session not found")
    return tech_lead.status()


@app.post("/api/lead/client/heartbeat")
def lead_heartbeat(request: Request):
    session = _lead_client_session(request)
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    result = tech_lead.heartbeat(supplied)
    if result is None or result["session_id"] != session["id"]:
        raise HTTPException(409, "Tech Lead control lease expired or was revoked")
    return result


@app.get("/api/lead/client/handover")
def lead_handover(request: Request):
    _lead_client_session(request, "handover:read")
    statuses = ("queued", "planning", "assigned", "running", "validating", "recovering", "handoff_ready",
                "awaiting_pr_approval", "publishing", "waiting_for_contract", "waiting_for_scope")
    marks = ",".join("?" for _ in statuses)
    with db.connect() as con:
        rows = con.execute(f"""SELECT t.id FROM tasks t WHERE t.status IN ({marks})
            ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
            t.created_at,t.id""", statuses).fetchall()
        contract_rows = con.execute("SELECT id,title,body,version,state,approved_at FROM contracts WHERE state='approved' ORDER BY title").fetchall()
    snapshot_tasks = [task_row(row["id"]) for row in rows]
    return {"contract_version": tech_lead.CONTRACT_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
            "tasks": snapshot_tasks,
            "pending_review": [task for task in snapshot_tasks if task["status"] == "awaiting_pr_approval"],
            "contracts": [dict(row) for row in contract_rows], "fleet": fleet_status()}


@app.post("/api/lead/client/tasks", status_code=202)
def lead_create_task(body: TaskCreate, request: Request):
    session = _lead_client_session(request, "task:create")
    if body.preferred_agent_id:
        agent = registry.get(body.preferred_agent_id)
        if agent and agent["adapter"] == "provider":
            raise HTTPException(403, "A Tech Lead session cannot grant provider data-transfer consent")
    task = create_task(body)
    db.event(task["id"], "tech_lead_task_created", "Task created by the active Tech Lead session.",
             {"session_id": session["id"], "client_id": session["client_id"]})
    return task


@app.get("/api/lead/client/tasks/{task_id}/diff")
def lead_task_diff(task_id: str, request: Request, offset: int = Query(default=0, ge=0),
                   limit: int = Query(default=250_000, ge=1, le=250_000)):
    session = _lead_client_session(request, "task:review")
    task_row(task_id)
    db.event(task_id, "tech_lead_diff_accessed", "Tech Lead reviewed the task diff.", {"session_id": session["id"]})
    return task_diff_preview(task_id, offset, limit)


@app.post("/api/lead/client/tasks/{task_id}/reviews", status_code=201)
def lead_review_task(task_id: str, body: LeadReview, request: Request):
    session = _lead_client_session(request, "task:review")
    task_row(task_id)
    return tech_lead.record_review(task_id, session["id"], body.decision, body.comments)


@app.get("/api/tasks/{task_id}/reviews")
def task_reviews(task_id: str):
    task_row(task_id)
    return tech_lead.reviews(task_id)


def decode_task(row: dict) -> dict:
    for key in ("required_capabilities", "file_globs", "dependencies", "validation", "acceptance_criteria"):
        row[key] = json.loads(row[key])
    row["pr_approved"] = bool(row["pr_approved"])
    row["provider_data_consent"] = bool(row.get("provider_data_consent", 0))
    row["base_branch"] = row.get("base_branch") or row.get("repository_default_branch")
    return row


def task_row(task_id: str) -> dict:
    with db.connect() as con:
        row = con.execute("""SELECT t.*, r.name repository_name, r.remote repository_remote,
            r.default_branch repository_default_branch,r.validation_profile repository_validation_profile,
            r.validation_command repository_validation_command,a.name agent_name
            FROM tasks t JOIN repositories r ON r.id=t.repository_id LEFT JOIN agents a ON a.id=t.assigned_agent_id
            WHERE t.id=?""", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Task not found")
    result = decode_task(dict(row))
    result["repository_validation_command"] = json.loads(result["repository_validation_command"] or "[]")
    return result


@app.get("/api/health")
def health():
    try:
        with db.connect() as con:
            database_check = con.execute("PRAGMA quick_check").fetchone()[0]
            active_tasks = con.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('planning','assigned','running','validating','recovering','publishing')").fetchone()[0]
            repository_count = con.execute("SELECT COUNT(*) FROM repositories WHERE enabled=1").fetchone()[0]
        agent_rows = registry.list()
        database_ok = database_check == "ok"
        return {"ok": database_ok, "database": {"ok": database_ok, "check": database_check},
                "scheduler_active": not scheduler._stopping, "active_tasks": active_tasks,
                "enabled_repositories": repository_count,
                "agents": {"enabled": sum(1 for agent in agent_rows if agent.get("enabled")),
                           "available": sum(1 for agent in agent_rows if agent.get("enabled") and agent.get("available"))}}
    except Exception as exc:
        return {"ok": False, "database": {"ok": False, "check": type(exc).__name__},
                "scheduler_active": not scheduler._stopping}


@app.get("/api/storage")
def storage():
    try:
        return storage_snapshot()
    except Exception as exc:
        raise HTTPException(500, f"Could not read local storage metrics: {type(exc).__name__}") from exc


@app.get("/api/storage/checkpoint-preview")
def checkpoint_preview():
    try:
        preview = portability.checkpoint_preview()
        for repo in preview["repositories"]:
            repo.pop("path", None)
            repo.pop("fingerprint", None)
            repo.pop("refs", None)
            for worktree in repo["worktrees"]:
                worktree.pop("path", None)
                worktree.pop("fingerprint", None)
                worktree.pop("refs", None)
        return preview
    except portability.CheckpointError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, "Could not inspect project checkpoint state") from exc


@app.post("/api/storage/checkpoints")
def create_project_checkpoint(body: ProjectCheckpointCreate):
    checkpoint_dir = settings.data_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for entry in checkpoint_dir.iterdir():
        if entry.is_symlink() or not entry.is_file() or entry.suffix != ".devagent":
            continue
        try:
            if now - entry.stat().st_mtime > 2 * 24 * 60 * 60:
                entry.unlink()
        except OSError:
            pass
    checkpoint_id = uuid.uuid4().hex
    path = checkpoint_dir / f"{checkpoint_id}.devagent"
    try:
        result = portability.create_checkpoint(path, include_uncommitted=body.include_uncommitted)
        return {"id": checkpoint_id, "filename": path.name, "size_bytes": result["size_bytes"],
                "sha256": result["sha256"], "repository_count": result["repository_count"],
                "uncommitted_included": result["uncommitted_included"],
                "omitted_uncommitted_paths": result["omitted_uncommitted_paths"]}
    except (portability.CheckpointError, FileExistsError) as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(507, "Could not write the project checkpoint; check free disk space and permissions.") from exc


@app.get("/api/storage/checkpoints/{checkpoint_id}")
def download_project_checkpoint(checkpoint_id: str):
    if not re.fullmatch(r"[0-9a-f]{32}", checkpoint_id):
        raise HTTPException(404, "Checkpoint not found")
    path = settings.data_dir / "checkpoints" / f"{checkpoint_id}.devagent"
    if path.is_symlink() or not path.is_file():
        raise HTTPException(404, "Checkpoint not found")
    return FileResponse(path, filename=path.name, media_type="application/vnd.devagent.checkpoint",
                        background=BackgroundTask(path.unlink, missing_ok=True))


@app.delete("/api/storage/checkpoints/{checkpoint_id}")
def delete_project_checkpoint(checkpoint_id: str):
    if not re.fullmatch(r"[0-9a-f]{32}", checkpoint_id):
        raise HTTPException(404, "Checkpoint not found")
    path = settings.data_dir / "checkpoints" / f"{checkpoint_id}.devagent"
    if path.is_symlink() or not path.is_file():
        raise HTTPException(404, "Checkpoint not found")
    path.unlink()
    return {"deleted": True}


@app.post("/api/storage/checkpoints/restore")
def restore_project_checkpoint(body: ProjectCheckpointRestore):
    destination_parent = Path(body.destination_parent).expanduser()
    name = f"DevAgent-Restored-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    destination = destination_parent / name
    try:
        result = portability.restore_checkpoint(Path(body.checkpoint_path), destination)
        return result
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except portability.CheckpointError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(507, "Could not restore checkpoint; check free disk space and permissions.") from exc


@app.get("/api/fleet")
def fleet_status():
    config = fleet_manager.settings()
    active_states = ("planning", "assigned", "running", "validating", "recovering", "publishing")
    marks = ",".join("?" for _ in active_states)
    with db.connect() as con:
        active = [dict(row) for row in con.execute(f"""SELECT t.id AS task_id,t.title,t.status,
                t.assigned_agent_id,a.name AS agent_name,r.name AS repository_name,
                (SELECT runs.id FROM runs WHERE runs.task_id=t.id ORDER BY runs.started_at DESC,runs.rowid DESC LIMIT 1) AS run_id
            FROM tasks t LEFT JOIN agents a ON a.id=t.assigned_agent_id
            LEFT JOIN repositories r ON r.id=t.repository_id WHERE t.status IN ({marks})
            ORDER BY t.created_at,t.id""", active_states).fetchall()]
        queued = con.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('queued','waiting_for_scope','waiting_for_contract')").fetchone()[0]
        cooldowns = [dict(row) for row in con.execute("""SELECT adapter,executable,reason,expires_at
            FROM credential_cooldowns WHERE julianday(expires_at)>julianday('now') ORDER BY expires_at""").fetchall()]
    free_disk = fleet_manager.disk_free_bytes()
    reserved = config["min_free_disk_bytes"] + len(active) * config["disk_reserve_per_run_bytes"]
    return {"settings": config, "active_runs": active, "active_run_count": len(active), "queued_tasks": queued,
            "credential_cooldowns": cooldowns,
            "draining": len(active) > config["max_active_runs"],
            "builds_active": orchestrator.build_slots.active,
            "disk": {"free_bytes": free_disk, "required_reserve_bytes": reserved,
                     "can_admit_run": free_disk >= reserved + config["disk_reserve_per_run_bytes"]}}


@app.patch("/api/fleet")
def update_fleet(body: FleetSettingsUpdate):
    try:
        values = fleet_manager.update_settings(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.event(None, "fleet_settings_updated", "Fleet capacity settings were updated.",
             {key: values[key] for key, value in body.model_dump().items() if value is not None})
    return fleet_status()


@app.get("/api/setup/status")
def setup_status():
    with db.connect() as con:
        repositories = con.execute("SELECT COUNT(*) FROM repositories WHERE enabled=1").fetchone()[0]
    return {"needs_onboarding": repositories == 0, "registered_repositories": repositories,
            "data_directory": str(db.DATA_ROOT), "database_path": str(db.DB_PATH),
            "api_host": settings.api_host, "api_port": settings.api_port}


@app.post("/api/repositories/preflight")
def repository_preflight(body: RepositoryPreflight):
    path = Path(body.path).expanduser()
    if not path.is_dir():
        raise HTTPException(422, "Selected path is not an existing directory")
    root = path.resolve()
    top = run(["git", "rev-parse", "--show-toplevel"], root)
    if top.returncode != 0:
        raise HTTPException(422, "Selected directory is not inside a Git repository")
    repository_root = Path(top.stdout.strip()).resolve()
    branch_validation = None
    try:
        branch_validation = validate_repository_base(
            {"path": str(repository_root), "default_branch": body.default_branch}, body.default_branch)
        branch_error = None
    except ValueError as exc:
        branch_error = str(exc)
    dirty = bool(run(["git", "status", "--porcelain"], repository_root).stdout.strip())
    profile = body.validation_profile
    if profile == "auto":
        profile = "maven" if (repository_root / "pom.xml").is_file() else "unsupported"
    executable = None
    executable_available = None
    if body.validation_command:
        command_head = body.validation_command[0]
        executable = shutil.which(command_head)
        if executable is None and (Path(command_head).is_absolute() or "/" in command_head):
            candidate = (repository_root / command_head).resolve()
            executable = str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None
        executable_available = executable is not None
    elif profile in {"maven", "auto"}:
        executable = shutil.which("mvn") or (str(repository_root / "mvnw") if (repository_root / "mvnw").is_file() else None)
        executable_available = executable is not None
    disk = shutil.disk_usage(db.DATA_ROOT if db.DATA_ROOT.exists() else repository_root)
    author_name, author_email = git_author(str(repository_root))
    detected_agents = [{"name": agent["name"], "adapter": agent["adapter"], "available": agent["available"],
                        "availability_note": agent["availability_note"]} for agent in registry.list()]
    return {"repository_root": str(repository_root), "default_branch": body.default_branch,
            "base_sha": branch_validation, "base_error": branch_error, "working_tree_dirty": dirty,
            "dirty_base_note": "Task worktrees use committed base only; uncommitted source-checkout changes are not copied." if dirty else None,
            "git_author": {"name": author_name, "email": author_email},
            "validation_profile": profile, "validation_command": body.validation_command,
            "validator_executable": executable, "validator_available": executable_available,
            "agent_tools": detected_agents,
            "free_disk_bytes": disk.free,
            "warnings": (["The selected base branch is unavailable; fetch or choose an existing branch."] if branch_error else [])
                        + (["No automatic validator is configured for this repository."] if profile == "unsupported" else [])
                        + (["Configured validator executable was not found."] if executable_available is False else [])
                        + (["No supported Agent CLI or handoff target has been detected yet."] if not detected_agents else [])}


@app.post("/api/storage/cleanup")
def cleanup_storage(max_age_hours: float = 24.0, dry_run: bool = True):
    try:
        return worktrees.prune_old(max_age_hours=max_age_hours, dry_run=dry_run)
    except Exception as exc:
        raise HTTPException(500, f"Could not prune worktrees: {type(exc).__name__}: {exc}") from exc


@app.post("/api/storage/prune-events")
def prune_event_log(dry_run: bool = False):
    try:
        return prune_events(dry_run=dry_run)
    except Exception as exc:
        raise HTTPException(500, f"Could not prune events: {type(exc).__name__}: {exc}") from exc


@app.get("/api/agents")
def agents():
    return registry.list()


@app.get("/api/providers")
def provider_connections():
    return providers.list_connections()


@app.get("/api/providers/credential-store")
def provider_credential_store_status():
    return providers.credential_store_status()


@app.post("/api/providers", status_code=201)
def create_provider_connection(body: ProviderConnectionCreate):
    api_key = body.api_key.get_secret_value() if body.api_key else None
    try:
        return providers.create_connection(body.provider, body.name, body.model, api_key, body.endpoint)
    except providers.ProviderError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.patch("/api/providers/{connection_id}")
def update_provider_connection(connection_id: str, body: ProviderConnectionUpdate):
    api_key = body.api_key.get_secret_value() if body.api_key else None
    try:
        connection = providers.update_connection(connection_id, name=body.name, model=body.model, api_key=api_key)
    except providers.ProviderError as exc:
        raise HTTPException(422, str(exc)) from exc
    if connection is None:
        raise HTTPException(404, "Provider connection not found")
    return connection


@app.post("/api/providers/{connection_id}/check")
def check_provider_connection(connection_id: str):
    try:
        connection = providers.check_connection(connection_id)
    except providers.ProviderError as exc:
        if str(exc) == "Provider connection not found":
            raise HTTPException(404, str(exc)) from exc
        raise HTTPException(422, str(exc)) from exc
    db.event(None, "provider_connection_checked", f"Checked {connection['provider']} provider connection.",
             {"provider": connection["provider"], "state": connection["state"], "model_count": len(connection["models"])})
    return connection


@app.delete("/api/providers/{connection_id}", status_code=204)
def delete_provider_connection(connection_id: str):
    try:
        agent_id = f"provider-{connection_id}"
        with db.connect() as con:
            active = con.execute("""SELECT id,title,status FROM tasks
                WHERE status IN ('queued','planning','waiting_for_contract','waiting_for_scope','assigned','running','validating',
                    'recovering','handoff_ready','awaiting_pr_approval','publishing')
                  AND (assigned_agent_id=? OR preferred_agent_id=?) ORDER BY created_at""",
                (agent_id, agent_id)).fetchall()
        if active:
            raise HTTPException(409, {"message": "Provider worker has active tasks; finish or cancel them before removal.",
                                      "tasks": [dict(row) for row in active]})
        if not providers.delete_connection(connection_id):
            raise HTTPException(404, "Provider connection not found")
    except HTTPException:
        raise
    except providers.ProviderError as exc:
        raise HTTPException(503, str(exc)) from exc
    return None


@app.post("/api/agents/refresh")
def refresh_agents():
    return registry.refresh()


@app.post("/api/agents/{agent_id}/check-token")
def check_agent_token(agent_id: str):
    agent = registry.check_token(agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    db.event(None, "agent_credential_checked", f"Credential status checked for {agent['name']}.",
             {"status": agent["credential_status"]}, agent_id=agent_id)
    return agent


@app.delete("/api/agents/{agent_id}")
def remove_agent(agent_id: str):
    agent = registry.get(agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    with db.connect() as con:
        active = con.execute("""SELECT id,title,status FROM tasks
            WHERE status IN ('queued','planning','waiting_for_contract','waiting_for_scope','assigned','running','validating',
                'recovering','handoff_ready','awaiting_pr_approval','publishing')
              AND (assigned_agent_id=? OR preferred_agent_id=?) ORDER BY created_at""",
            (agent_id, agent_id)).fetchall()
    if active:
        raise HTTPException(409, {"message": "Agent has active tasks; finish or cancel them before removal.",
                                  "tasks": [dict(row) for row in active]})
    updated = registry.set_enabled(agent_id, False)
    db.event(None, "agent_removed", f"{agent['name']} was removed from future scheduling.", agent_id=agent_id)
    return updated


@app.post("/api/agents/{agent_id}/restore")
def restore_agent(agent_id: str):
    updated = registry.set_enabled(agent_id, True)
    if updated is None:
        raise HTTPException(404, "Agent not found")
    db.event(None, "agent_restored", f"{updated['name']} was restored to scheduling.", agent_id=agent_id)
    return updated


@app.post("/api/agents", status_code=201)
def register_agent(body: AgentCreate):
    if body.adapter in {"codex", "copilot", "antigravity", "exec"} and not body.command:
        raise HTTPException(422, "Executable command array is required for CLI adapters")
    agent_id = re.sub(r"[^a-z0-9-]+", "-", body.name.lower()).strip("-") + "-" + uuid.uuid4().hex[:6]
    payload = body.model_dump() | {"id": agent_id}
    try:
        return registry.register(payload)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/repositories")
def repositories():
    with db.connect() as con:
        return [dict(row) | {"validation_command": json.loads(row["validation_command"] or "[]")}
                for row in con.execute("SELECT * FROM repositories ORDER BY name").fetchall()]


@app.get("/api/repositories/{repository_id}/branches")
def repository_branches(repository_id: str):
    try:
        repo = repository(repository_id)
        branches = list_repository_branches(repo)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"default_branch": repo["default_branch"], "branches": branches}


@app.get("/api/repositories/{repository_id}/sync-status")
def repository_sync_status(repository_id: str, target_branch: str | None = None):
    try:
        repo = repository(repository_id)
        return check_repository_sync(repo, target_branch)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/repositories/{repository_id}/rag/index")
def rag_index(repository_id: str):
    from . import rag
    try:
        return rag.index_repository(repository_id, repository(repository_id)["path"])
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/repositories/{repository_id}/rag/search")
def rag_search(repository_id: str, q: str, limit: int = 6):
    from . import rag
    try:
        rag.index_repository(repository_id, repository(repository_id)["path"])
        return rag.search(repository_id, q, max(1, min(limit, 20)))
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/repositories/{repository_id}/code-context")
def repository_code_context(repository_id: str, q: str = Query(min_length=1, max_length=500),
                            limit: int = Query(default=6, ge=1, le=12),
                            max_chars: int = Query(default=4000, ge=256, le=8000)):
    from . import code_context
    try:
        repo = repository(repository_id)
        return code_context.search(repository_id, repo["path"], q, max_chars=max_chars, limit=limit)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/repositories", status_code=201)
def add_repository(body: RepositoryCreate):
    try:
        return register_repository(body.name, body.path, body.default_branch,
                                   body.validation_profile, body.validation_command)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/contracts")
def contracts():
    with db.connect() as con:
        return contracts_manager.list()


@app.post("/api/contracts", status_code=201)
def create_contract(body: ContractCreate):
    return contracts_manager.propose(body.title, body.body)


@app.post("/api/contracts/{contract_id}/approve")
def approve_contract(contract_id: str):
    if not contracts_manager.approve(contract_id):
        raise HTTPException(404, "Proposed contract not found")
    return {"id": contract_id, "state": "approved"}


@app.get("/api/tasks")
def tasks(limit: int = 100):
    return reporting.tasks(limit)


@app.post("/api/tasks", status_code=202)
def create_task(body: TaskCreate):
    try:
        repo = repository(body.repository_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    try:
        base_branch = body.base_branch or repo["default_branch"]
        base_sha = validate_repository_base(repo, base_branch)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.preferred_agent_id:
        agent = registry.get(body.preferred_agent_id)
        if agent is None or not agent["enabled"]:
            raise HTTPException(422, "Preferred agent is unknown or disabled")
        if not set(body.required_capabilities).issubset(set(agent["capabilities"])):
            raise HTTPException(422, "Preferred agent does not advertise the requested capabilities")
        if agent["adapter"] == "provider":
            connection_id = (agent.get("command") or [None])[0]
            connection = providers.get_connection(connection_id) if connection_id else None
            if not connection:
                raise HTTPException(422, "Provider connection for this agent is unavailable")
            if connection["provider"] != "ollama" and not body.provider_data_consent:
                raise HTTPException(422, "Explicit consent is required to send scoped task content to this cloud provider")
            if not agent["available"]:
                raise HTTPException(422, "Provider worker is unavailable; check its credential and select a supported model")
    if body.contract_id:
        with db.connect() as con:
            contract = con.execute("SELECT state FROM contracts WHERE id=?", (body.contract_id,)).fetchone()
        if not contract:
            raise HTTPException(422, "Contract not found")
    if body.dependencies:
        placeholders = ",".join("?" for _ in body.dependencies)
        with db.connect() as con:
            found = con.execute(f"SELECT id,repository_id FROM tasks WHERE id IN ({placeholders})", body.dependencies).fetchall()
        if len(found) != len(set(body.dependencies)):
            raise HTTPException(422, "One or more dependency tasks do not exist")
        if any(row["repository_id"] != body.repository_id for row in found):
            raise HTTPException(422, "Task dependencies must belong to the same repository")
        if not body.contract_id:
            raise HTTPException(422, "Tasks with dependencies require an interface contract")
    planned = execution_engine.planner.plan(body.title, body.objective, body.file_globs,
                                           body.required_capabilities, body.acceptance_criteria)
    task_id = str(uuid.uuid4())
    with db.transaction() as con:
        con.execute("""INSERT INTO tasks(id,title,objective,acceptance_criteria,priority,repository_id,base_branch,base_sha,required_capabilities,file_globs,dependencies,
            contract_id,preferred_agent_id,provider_data_consent,max_retries) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task_id, planned.title, planned.objective, db.dumps(body.acceptance_criteria), body.priority,
             body.repository_id, base_branch, base_sha, db.dumps(planned.required_capabilities), db.dumps(planned.file_globs),
             db.dumps(body.dependencies), body.contract_id, body.preferred_agent_id,
             int(body.provider_data_consent), body.max_retries))
    db.event(task_id, "queued", "Task accepted by planner and queued for capability matching.",
             {"allowed_globs": body.file_globs, "required_capabilities": body.required_capabilities,
              "acceptance_criteria_count": len(body.acceptance_criteria), "priority": body.priority,
              "base_branch": base_branch, "provider_data_consent": body.provider_data_consent})
    return task_row(task_id)


@app.get("/api/tasks/{task_id}")
def get_task_details(task_id: str):
    task = task_row(task_id)
    with db.connect() as con:
        task["runs"] = [dict(r) for r in con.execute("SELECT * FROM runs WHERE task_id=? ORDER BY started_at,rowid", (task_id,)).fetchall()]
        lease = con.execute("SELECT task_id,run_id,agent_id,state,acquired_at,updated_at "
                            "FROM task_writer_leases WHERE task_id=?", (task_id,)).fetchone()
    task["writer_lease"] = dict(lease) if lease else None
    return task


@app.get("/api/tasks/{task_id}/events")
def task_events(task_id: str, after: int = 0):
    if not get_task(task_id):
        raise HTTPException(404, "Task not found")
    return reporting.events(task_id, after)


@app.get("/api/tasks/{task_id}/diff")
def task_diff_preview(task_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=250_000, ge=1, le=250_000)):
    task = task_row(task_id)
    commit = task.get("validation", {}).get("commit")
    if not task.get("worktree") or not commit:
        raise HTTPException(409, "Task has no validated commit to review")
    worktree = Path(task["worktree"]).resolve()
    allowed_root = (db.DATA_ROOT / "worktrees").resolve()
    if not worktree.is_relative_to(allowed_root):
        raise HTTPException(409, "Task worktree is outside the Orchestrator worktree area")
    if not worktree.is_dir():
        raise HTTPException(404, "Task worktree is no longer available")
    if not validated_revision_is_current(str(worktree), task.get("validation", {}).get("commit")):
        raise HTTPException(409, "Task worktree changed after validation; rerun review and validation")
    try:
        return worktrees.diff(str(worktree), str(commit), task.get("base_sha") or task.get("base_branch"),
                              max_chars=limit, offset=offset)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.websocket("/ws/tasks/{task_id}/events")
async def stream_events(socket: WebSocket, task_id: str, ticket: str, after: int = 0):
    expires = _ws_tickets.pop(ticket, 0)
    origin = socket.headers.get("origin", "")
    if expires <= time.monotonic() or origin not in ALLOWED_ORIGINS or not _host_is_loopback(socket.headers.get("host", "")):
        await socket.close(code=4401)
        return
    if not get_task(task_id):
        await socket.close(code=4404)
        return
    await socket.accept()
    last_id = max(0, after)
    try:
        while True:
            with db.connect() as con:
                rows = con.execute("SELECT * FROM events WHERE task_id=? AND id>? ORDER BY id LIMIT 100", (task_id, last_id)).fetchall()
            for row in rows:
                item = dict(row)
                item["details"] = json.loads(item["details"])
                await socket.send_json(item)
                last_id = item["id"]
            await asyncio.sleep(0.35)
    except WebSocketDisconnect:
        return


@app.get("/api/tasks/{task_id}/handoff", response_class=PlainTextResponse)
def handoff_file(task_id: str):
    task = task_row(task_id)
    if task["status"] not in {"handoff_ready", "handoff_complete"}:
        raise HTTPException(409, "Task is not ready for IDE handoff")
    file = db.DATA_ROOT / "handoffs" / f"{task_id}.md"
    if not file.exists():
        raise HTTPException(404, "Handoff package was not generated")
    return file.read_text(encoding="utf-8")


@app.post("/api/tasks/{task_id}/handoff-complete")
async def handoff_complete(task_id: str, body: CompleteHandoff):
    task = task_row(task_id)
    if task["status"] != "handoff_ready" or not task["worktree"]:
        raise HTTPException(409, "Task is not waiting for IDE completion")
    paths = await asyncio.to_thread(worktrees.changed_paths, task["worktree"], task.get("base_sha") or task.get("base_branch"))
    outside = validation_engine.inspect_scope(paths, task["file_globs"], registry.get(task["assigned_agent_id"])["file_globs"])
    secret_files = validation_engine.inspect_secrets(task["worktree"], paths)
    if outside or secret_files:
        raise HTTPException(409, {"out_of_scope": outside, "possible_secrets": secret_files})
    validation = await validation_engine.validate(
        task["worktree"], lambda kind, msg: log_event(task_id, kind, msg, agent_id=task["assigned_agent_id"]),
        task.get("repository_validation_profile", "auto"), task.get("repository_validation_command", []), paths=paths)
    if not validation["passed"]:
        set_task(task_id, "blocked" if validation.get("blocked") else "failed", error=validation["summary"], validation=validation)
        log_event(task_id, "validation_failed", validation["summary"], validation, task["assigned_agent_id"])
        raise HTTPException(422, validation)
    commit = await asyncio.to_thread(worktrees.commit, task["worktree"], paths, task_id, task["title"],
                                     task.get("base_sha") or task.get("base_branch"))
    validation.update({"changed_paths": paths, "commit": commit, "handoff_report": body.report, "scope_passed": True})
    set_task(task_id, "awaiting_pr_approval", validation=validation)
    log_event(task_id, "awaiting_pr_approval", "Handoff diff validated and committed; push/Draft PR requires approval.", validation,
              task["assigned_agent_id"])
    return task_row(task_id)


@app.post("/api/tasks/{task_id}/approve-draft-pr")
def approve_draft_pr(task_id: str, confirmation: str):
    task = task_row(task_id)
    if task["status"] != "awaiting_pr_approval":
        raise HTTPException(409, "Task is not ready for PR approval")
    if confirmation != f"CREATE DRAFT PR {task_id[:8]}":
        raise HTTPException(422, f"To approve, enter: CREATE DRAFT PR {task_id[:8]}")
    if not task["validation"].get("passed") or not task["validation"].get("scope_passed"):
        raise HTTPException(409, "Validation and scope check must pass before pushing")
    if not validated_revision_is_current(task["worktree"], task["validation"].get("commit")):
        raise HTTPException(409, "Task worktree changed after validation; rerun validation before publishing")
    repo = repository(task["repository_id"])
    if not is_github_remote(repo.get("remote")):
        raise HTTPException(422, "Draft PR publishing currently supports GitHub remotes only; no push was attempted")
    with db.transaction() as con:
        claimed = con.execute("""UPDATE tasks SET status='publishing',pr_approved=1,updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='awaiting_pr_approval'""", (task_id,))
    if claimed.rowcount != 1:
        raise HTTPException(409, "Another request already changed this task; refresh and review its current state")
    try:
        criteria = task.get("acceptance_criteria") or []
        body = (f"## Task\n{task['title']}\n\n{task['objective']}\n\n"
                + ("## Acceptance criteria\n" + "\n".join(f"- {item}" for item in criteria) + "\n\n" if criteria else "")
                + "## Validation\n"
                f"`{task['validation'].get('command') or 'validation'}` — passed\n\n"
                f"## Scope\n" + "\n".join(f"- `{p}`" for p in task["validation"].get("changed_paths", [])) +
                f"\n\nOrchestrator task: `{task_id}`. Draft PR created after explicit operator approval.")
        url = pr_manager.create_draft_after_approval(repo, task["worktree"], task["branch"],
                                                     task["base_branch"], task["title"], body)
        set_task(task_id, "draft_pr", pr_url=url, finished_at=__import__("datetime").datetime.now().isoformat())
        log_event(task_id, "draft_pr_created", f"Draft PR created: {url}", {"url": url})
        return task_row(task_id)
    except Exception as exc:
        set_task(task_id, "awaiting_pr_approval", error=str(exc))
        log_event(task_id, "publish_failed", str(exc))
        raise HTTPException(502, "Push or Draft PR failed. Review the task log before retrying.") from exc


@app.post("/api/tasks/{task_id}/complete-local")
def complete_task_locally(task_id: str):
    task = task_row(task_id)
    if task["status"] != "awaiting_pr_approval":
        raise HTTPException(409, "Task is not ready for local completion")
    if not task["validation"].get("passed") or not task["validation"].get("scope_passed"):
        raise HTTPException(409, "Validation and scope check must pass before completion")
    if not validated_revision_is_current(task["worktree"], task["validation"].get("commit")):
        raise HTTPException(409, "Task worktree changed after validation; rerun validation before completion")
    with db.transaction() as con:
        claimed = con.execute("""UPDATE tasks SET status='completed',finished_at=CURRENT_TIMESTAMP,
            updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='awaiting_pr_approval'""", (task_id,))
    if claimed.rowcount != 1:
        raise HTTPException(409, "Another request already changed this task; refresh its current state")
    db.event(task_id, "completed", "Task completed locally after validation; no push or pull request was created.",
             {"branch": task["branch"], "commit": task["validation"].get("commit")})
    return task_row(task_id)


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    task = task_row(task_id)
    job = scheduler.active.get(task_id)
    if job:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
    elif task["status"] in {"queued", "waiting_for_contract", "waiting_for_scope", "handoff_ready"}:
        set_task(task_id, "cancelled", error="Stopped by operator; worktree retained for review.")
        log_event(task_id, "cancelled", "Task cancelled by operator.")
    else:
        raise HTTPException(409, "Task is not cancellable in its current state")
    return task_row(task_id)


@app.get("/api/history")
def history(limit: int = 100):
    return reporting.history(limit)


DASHBOARD_DIST = Path(__file__).resolve().parents[2] / "dashboard" / "dist"
if DASHBOARD_DIST.is_dir():
    @app.get("/remote", include_in_schema=False)
    @app.get("/remote/", include_in_schema=False)
    def remote_viewer_index(request: Request):
        if not remote_pairing.trusted_ingress_peer_allowed(request):
            raise HTTPException(403, "Remote HTTPS ingress is not configured or request boundary is invalid")
        index = DASHBOARD_DIST / "index.html"
        if not index.is_file():
            raise HTTPException(404, "Remote viewer bundle is unavailable")
        html = index.read_text(encoding="utf-8")
        html = html.replace('src="/assets/', 'src="/remote/assets/').replace(
            'href="/assets/', 'href="/remote/assets/')
        return HTMLResponse(html, headers={
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                       "connect-src 'self' wss:; img-src 'self' data:; object-src 'none'; "
                                       "base-uri 'none'; frame-ancestors 'none'",
        })

    app.mount("/remote/assets", StaticFiles(directory=str(DASHBOARD_DIST / "assets")), name="remote-assets")

if DASHBOARD_DIST.exists() and os.getenv("CLINIC_ORCHESTRATOR_HEADLESS") != "1":
    app.mount("/", StaticFiles(directory=str(DASHBOARD_DIST), html=True), name="dashboard")


