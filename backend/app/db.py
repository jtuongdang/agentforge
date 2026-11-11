from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .settings import settings

ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = settings.data_dir
DB_PATH = Path(os.getenv("CLINIC_ORCHESTRATOR_DB", DATA_ROOT / "orchestrator.sqlite3")).expanduser()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, traceback):
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=20, factory=ClosingConnection)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    con = connect()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def rowdict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def backup_before_migration() -> Path | None:
    """Create a SQLite-consistent safety copy before applying a schema upgrade."""
    if not DB_PATH.is_file() or DB_PATH.stat().st_size == 0:
        return None
    with sqlite3.connect(DB_PATH, timeout=20) as source:
        version = source.execute("PRAGMA user_version").fetchone()[0]
        tables = source.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if version >= 5 or tables == 0:
            return None
        backup_dir = DB_PATH.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"pre-migration-v{version}-{time.time_ns()}.sqlite3"
        with sqlite3.connect(target) as destination:
            source.backup(destination)
    return target


def init_db() -> None:
    backup_before_migration()
    with transaction() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS repositories (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
            remote TEXT, default_branch TEXT NOT NULL DEFAULT 'main', enabled INTEGER NOT NULL DEFAULT 1,
            validation_profile TEXT NOT NULL DEFAULT 'auto', validation_command TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, adapter TEXT NOT NULL,
            command TEXT, capabilities TEXT NOT NULL DEFAULT '[]', file_globs TEXT NOT NULL DEFAULT '["**/*"]',
            max_parallel INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1,
            available INTEGER NOT NULL DEFAULT 0, availability_note TEXT NOT NULL DEFAULT '',
            last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS contracts (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL DEFAULT 'proposed',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, approved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, objective TEXT NOT NULL,
            acceptance_criteria TEXT NOT NULL DEFAULT '[]', priority TEXT NOT NULL DEFAULT 'normal',
            repository_id TEXT NOT NULL REFERENCES repositories(id), base_branch TEXT, base_sha TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            required_capabilities TEXT NOT NULL DEFAULT '[]', file_globs TEXT NOT NULL,
            dependencies TEXT NOT NULL DEFAULT '[]', contract_id TEXT REFERENCES contracts(id),
            preferred_agent_id TEXT REFERENCES agents(id), assigned_agent_id TEXT REFERENCES agents(id),
            branch TEXT, worktree TEXT, retries INTEGER NOT NULL DEFAULT 0, max_retries INTEGER NOT NULL DEFAULT 2,
            validation TEXT NOT NULL DEFAULT '{}', pr_url TEXT, pr_approved INTEGER NOT NULL DEFAULT 0,
            error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), agent_id TEXT NOT NULL REFERENCES agents(id),
            state TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at TEXT,
            exit_code INTEGER, report TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT REFERENCES tasks(id), agent_id TEXT,
            kind TEXT NOT NULL, message TEXT NOT NULL, details TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS lead_sessions (
            id TEXT PRIMARY KEY, client_id TEXT NOT NULL, client_name TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE, scopes TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL, last_seen TEXT NOT NULL, expires_at TEXT NOT NULL,
            revoked_at TEXT
        );
        CREATE TABLE IF NOT EXISTS lead_lease (
            id INTEGER PRIMARY KEY CHECK(id=1), session_id TEXT NOT NULL REFERENCES lead_sessions(id),
            expires_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS lead_reviews (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
            session_id TEXT NOT NULL REFERENCES lead_sessions(id), decision TEXT NOT NULL,
            comments TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS task_writer_leases (
            task_id TEXT PRIMARY KEY REFERENCES tasks(id), lease_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL, agent_id TEXT NOT NULL REFERENCES agents(id),
            state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','orphaned')),
            acquired_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS credential_cooldowns (
            credential_key TEXT PRIMARY KEY, adapter TEXT NOT NULL, executable TEXT NOT NULL,
            reason TEXT NOT NULL, expires_at TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, id);
        """)
        # Additive migration for existing local databases.
        task_columns = {row[1] for row in con.execute("PRAGMA table_info(tasks)").fetchall()}
        task_migrations = {
            "acceptance_criteria": "TEXT NOT NULL DEFAULT '[]'",
            "priority": "TEXT NOT NULL DEFAULT 'normal'",
            "base_branch": "TEXT",
            "base_sha": "TEXT",
        }
        for name, declaration in task_migrations.items():
            if name not in task_columns:
                con.execute(f"ALTER TABLE tasks ADD COLUMN {name} {declaration}")
        repository_columns = {row[1] for row in con.execute("PRAGMA table_info(repositories)").fetchall()}
        repository_migrations = {
            "validation_profile": "TEXT NOT NULL DEFAULT 'auto'",
            "validation_command": "TEXT NOT NULL DEFAULT '[]'",
        }
        for name, declaration in repository_migrations.items():
            if name not in repository_columns:
                con.execute(f"ALTER TABLE repositories ADD COLUMN {name} {declaration}")
        columns = {row[1] for row in con.execute("PRAGMA table_info(agents)").fetchall()}
        migrations = {
            "credential_status": "TEXT NOT NULL DEFAULT 'unchecked'",
            "credential_checked_at": "TEXT",
            "credential_note": "TEXT NOT NULL DEFAULT ''",
            "credential_usage": "TEXT NOT NULL DEFAULT '{}'",
        }
        for name, declaration in migrations.items():
            if name not in columns:
                con.execute(f"ALTER TABLE agents ADD COLUMN {name} {declaration}")
        con.execute("PRAGMA user_version=5")


def event(task_id: str | None, kind: str, message: str, details: Any = None, agent_id: str | None = None) -> None:
    with transaction() as con:
        con.execute("INSERT INTO events(task_id, agent_id, kind, message, details) VALUES(?,?,?,?,?)",
                    (task_id, agent_id, kind, message, dumps(details or {})))

