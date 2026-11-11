from __future__ import annotations

import asyncio
import sqlite3
import shutil
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
