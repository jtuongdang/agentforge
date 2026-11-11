from __future__ import annotations

import os
import shutil
from pathlib import Path
from datetime import datetime, timezone

from . import db


DATA_ROOT = db.DATA_ROOT
ACTIVE_STATUSES = ("planning", "assigned", "running", "validating", "recovering", "publishing")


def _tree_size(root: Path) -> int:
    """Sum regular file sizes without following symlinks."""
    if not root.exists():
        return 0
    total = 0
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def storage_snapshot() -> dict:
    data_root = DATA_ROOT
    data_root.mkdir(parents=True, exist_ok=True)
    category_bytes = {"database": 0, "worktrees": 0, "handoffs": 0, "other": 0}
    try:
        entries = list(os.scandir(data_root))
    except OSError:
        entries = []

    database_names = {db.DB_PATH.name, f"{db.DB_PATH.name}-wal", f"{db.DB_PATH.name}-shm"}
    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            if entry.name in database_names:
                category = "database"
            elif entry.name == "worktrees":
                category = "worktrees"
            elif entry.name == "handoffs":
                category = "handoffs"
            else:
                category = "other"
            category_bytes[category] += _tree_size(Path(entry.path))
        except OSError:
            continue

    database_files = {}
    for suffix, label in (("", "main"), ("-wal", "wal"), ("-shm", "shared_memory")):
        file_path = Path(f"{db.DB_PATH}{suffix}")
        try:
            database_files[label] = file_path.stat().st_size if file_path.is_file() else 0
        except OSError:
            database_files[label] = 0
    category_bytes["database"] = sum(database_files.values())

    # An explicitly configured database may live outside the Orchestrator data folder.
    if not db.DB_PATH.resolve().is_relative_to(data_root.resolve()):
        category_bytes["database"] = sum(database_files.values())
    data_bytes = sum(category_bytes.values())
    try:
        disk = shutil.disk_usage(db.DB_PATH.parent)
        disk_info = {"total_bytes": disk.total, "free_bytes": disk.free,
                     "used_percent": round(((disk.total - disk.free) / disk.total) * 100, 1) if disk.total else 0}
    except OSError:
        disk_info = {"total_bytes": 0, "free_bytes": 0, "used_percent": 0}

    with db.connect() as con:
        counts = {"repositories": con.execute("SELECT COUNT(*) FROM repositories").fetchone()[0],
                  "agents": con.execute("SELECT COUNT(*) FROM agents").fetchone()[0],
                  "tasks": con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
                  "runs": con.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
                  "events": con.execute("SELECT COUNT(*) FROM events").fetchone()[0]}
        active_marks = ",".join("?" for _ in ACTIVE_STATUSES)
        counts["active_tasks"] = con.execute(
            f"SELECT COUNT(*) FROM tasks WHERE status IN ({active_marks})", ACTIVE_STATUSES).fetchone()[0]
        page_count = con.execute("PRAGMA page_count").fetchone()[0]
        page_size = con.execute("PRAGMA page_size").fetchone()[0]
        free_pages = con.execute("PRAGMA freelist_count").fetchone()[0]

    return {"measured_at": datetime.now(timezone.utc).isoformat(), "data_bytes": data_bytes,
            "categories": category_bytes, "database_files": database_files,
            "sqlite_logical_bytes": page_count * page_size, "sqlite_unused_bytes": free_pages * page_size,
            "disk": disk_info, "counts": counts}


NOISY_LOG_PATTERNS = ("%\\_delta%", "step\\_update%", "init:%", "item.started%", "turn.started%", "thread.started%",
                      "20__-__-__T%")  # codex stderr WARN/ERROR timestamp lines


def prune_events(noise_hours: float = 24.0, build_days: float = 3.0, log_days: float = 7.0,
                 dry_run: bool = False, vacuum: bool = True) -> dict:
    """Trim high-volume event noise; milestone events (queued/completed/failed/validation...) are never touched.

    - streaming agent_log noise (deltas, step updates, stderr lines) older than ``noise_hours``
    - Maven ``build`` lines older than ``build_days``
    - any remaining agent_log older than ``log_days``
    Events of active tasks are always kept.
    """
    marks = ",".join("?" for _ in ACTIVE_STATUSES)
    keep = f"(task_id IS NULL OR task_id NOT IN (SELECT id FROM tasks WHERE status IN ({marks})))"
    noise = " OR ".join("message LIKE ? ESCAPE '\\'" for _ in NOISY_LOG_PATTERNS)
    rules = {
        "noise": (f"kind='agent_log' AND ({noise}) AND created_at < datetime('now', ?) AND {keep}",
                  [*NOISY_LOG_PATTERNS, f"-{noise_hours} hours", *ACTIVE_STATUSES]),
        "build": (f"kind='build' AND created_at < datetime('now', ?) AND {keep}",
                  [f"-{build_days} days", *ACTIVE_STATUSES]),
        "old_logs": (f"kind='agent_log' AND created_at < datetime('now', ?) AND {keep}",
                     [f"-{log_days} days", *ACTIVE_STATUSES]),
    }
    before = DB_FILE_SIZE()
    deleted = {}
    with db.connect() as con:
        for name, (where, params) in rules.items():
            if dry_run:
                deleted[name] = con.execute(f"SELECT COUNT(*) FROM events WHERE {where}", params).fetchone()[0]
            else:
                deleted[name] = con.execute(f"DELETE FROM events WHERE {where}", params).rowcount
        if not dry_run:
            con.commit()
    if vacuum and not dry_run and sum(deleted.values()) > 0:
        con = db.connect()
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.execute("VACUUM")
        finally:
            con.close()
    after = DB_FILE_SIZE()
    return {"deleted": deleted, "total_deleted": sum(deleted.values()), "dry_run": dry_run,
            "db_bytes_before": before, "db_bytes_after": after, "freed_bytes": max(0, before - after)}


def DB_FILE_SIZE() -> int:
    try:
        return Path(db.DB_PATH).stat().st_size
    except OSError:
        return 0
