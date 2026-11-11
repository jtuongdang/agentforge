from __future__ import annotations

import asyncio
import json
import sqlite3
import shutil
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db


DEFAULTS: dict[str, int] = {
    "max_active_runs": 2,
    "max_parallel_builds": 1,
    "max_per_credential": 2,
    "min_free_disk_bytes": 1 * 1024**3,
    "disk_reserve_per_run_bytes": 512 * 1024**2,
}


def settings(con: sqlite3.Connection | None = None) -> dict[str, int]:
    if con is None:
        with db.connect() as connection:
            rows = connection.execute("SELECT key,value FROM system_settings WHERE key LIKE 'fleet.%'").fetchall()
    else:
        rows = con.execute("SELECT key,value FROM system_settings WHERE key LIKE 'fleet.%'").fetchall()
    values = {row["key"].removeprefix("fleet."): int(row["value"]) for row in rows}
    return DEFAULTS | values


def update_settings(updates: dict[str, int | None]) -> dict[str, int]:
    values = {key: value for key, value in updates.items() if value is not None}
    current = settings()
    candidate = current | values
    validate_settings(candidate)
    with db.transaction() as con:
        for key, value in values.items():
            con.execute("""INSERT INTO system_settings(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP""",
                (f"fleet.{key}", str(value)))
    return candidate


def validate_settings(values: dict[str, int]) -> None:
    bounds = {
        "max_active_runs": (1, 32),
        "max_parallel_builds": (1, 16),
        "max_per_credential": (1, 16),
        "min_free_disk_bytes": (256 * 1024**2, 1024**4),
        "disk_reserve_per_run_bytes": (64 * 1024**2, 1024**4),
    }
    for key, (minimum, maximum) in bounds.items():
        value = values.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")


class BuildSlotGate:
    """Small local limiter whose capacity follows persisted settings without killing active builds."""

    def __init__(self) -> None:
        self.active = 0

    async def acquire(self) -> None:
        while True:
            if self.active < settings()["max_parallel_builds"]:
                self.active += 1
                return
            await asyncio.sleep(0.2)

    def release(self) -> None:
        self.active = max(0, self.active - 1)


def disk_free_bytes() -> int:
    root = db.DATA_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(root).free


def credential_key(agent: dict[str, Any]) -> tuple[str, str]:
    command = agent.get("command") or []
    executable = str(command[0]) if command else agent["adapter"]
    return agent["adapter"], executable


def credential_id(agent: dict[str, Any]) -> str:
    return db.dumps(list(credential_key(agent)))


def cooldown_remaining(agent: dict[str, Any]) -> float:
    with db.connect() as con:
        row = con.execute("SELECT MAX(0,(julianday(expires_at)-julianday('now'))*86400) "
                           "FROM credential_cooldowns WHERE credential_key=?", (credential_id(agent),)).fetchone()
    return float((row[0] if row else None) or 0.0)


def set_credential_cooldown(agent: dict[str, Any], seconds: float, reason: str) -> None:
    duration = min(60.0, max(0.0, seconds))
    if duration <= 0:
        return
    adapter, executable = credential_key(agent)
    expires = (datetime.now(timezone.utc) + timedelta(seconds=duration)).isoformat()
    with db.transaction() as con:
        con.execute("""INSERT INTO credential_cooldowns(credential_key,adapter,executable,reason,expires_at)
            VALUES(?,?,?,?,?) ON CONFLICT(credential_key) DO UPDATE SET adapter=excluded.adapter,
            executable=excluded.executable,reason=excluded.reason,expires_at=excluded.expires_at,
            updated_at=CURRENT_TIMESTAMP""", (credential_id(agent), adapter, executable, reason[:160], expires))


def transfer_task_to_rescue(task_id: str, current_agent_id: str, next_agent: dict[str, Any],
                            lease_id: str, current_run_id: str, next_run_id: str) -> bool:
    """Atomically budget and transfer a task lease after its previous worker has exited."""
    active_states = ("planning", "assigned", "running", "validating", "recovering", "publishing")
    marks = ",".join("?" for _ in active_states)
    with db.transaction() as con:
        con.execute("BEGIN IMMEDIATE")
        task = con.execute("SELECT status,assigned_agent_id,required_capabilities FROM tasks WHERE id=?", (task_id,)).fetchone()
        target = con.execute("SELECT enabled,available,max_parallel,adapter,command,capabilities FROM agents WHERE id=?",
                             (next_agent["id"],)).fetchone()
        if (not task or task["status"] not in active_states or task["assigned_agent_id"] != current_agent_id
                or not target or not target["enabled"] or not target["available"] or target["adapter"] == "handoff"):
            return False
        if not set(json.loads(task["required_capabilities"])).issubset(json.loads(target["capabilities"])):
            return False
        target_command = json.loads(target["command"] or "[]")
        next_adapter, next_executable = credential_key(next_agent)
        target_credential = (target["adapter"], str(target_command[0]) if target_command else target["adapter"])
        if target_credential != (next_adapter, next_executable):
            return False
        cooldown = con.execute("SELECT julianday(expires_at)>julianday('now') FROM credential_cooldowns WHERE credential_key=?",
                               (db.dumps([next_adapter, next_executable]),)).fetchone()
        if cooldown and cooldown[0]:
            return False
        config = settings(con)
        credential_runs = con.execute(f"""SELECT COUNT(*) FROM tasks t JOIN agents a ON a.id=t.assigned_agent_id
            WHERE t.status IN ({marks}) AND t.id<>? AND a.adapter=?
            AND COALESCE(json_extract(a.command,'$[0]'),a.adapter)=?""",
            (*active_states, task_id, next_adapter, next_executable)).fetchone()[0]
        agent_runs = con.execute(f"SELECT COUNT(*) FROM tasks WHERE status IN ({marks}) "
                                 "AND assigned_agent_id=? AND id<>?", (*active_states, next_agent["id"], task_id)).fetchone()[0]
        if credential_runs >= config["max_per_credential"] or agent_runs >= target["max_parallel"]:
            return False
        moved = con.execute("""UPDATE task_writer_leases SET run_id=?,agent_id=?,updated_at=CURRENT_TIMESTAMP
            WHERE task_id=? AND lease_id=? AND run_id=? AND agent_id=? AND state='active'""",
            (next_run_id, next_agent["id"], task_id, lease_id, current_run_id, current_agent_id))
        if moved.rowcount != 1:
            return False
        con.execute("INSERT INTO runs(id,task_id,agent_id,state) VALUES(?,?,?,'running')",
                    (next_run_id, task_id, next_agent["id"]))
        con.execute("UPDATE tasks SET assigned_agent_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (next_agent["id"], task_id))
    return True
