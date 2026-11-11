"""Persistent single-writer leases for task worktrees."""
from __future__ import annotations

import sqlite3

from . import db


class WriterLeaseManager:
    """Serialize worker ownership of one task worktree across scheduler instances."""

    @staticmethod
    def acquire(task_id: str, run_id: str, agent_id: str, lease_id: str) -> bool:
        try:
            with db.transaction() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("""INSERT INTO task_writer_leases(task_id,lease_id,run_id,agent_id,state)
                    VALUES(?,?,?,?,'active')""", (task_id, lease_id, run_id, agent_id))
            return True
        except sqlite3.IntegrityError:
            return False

    @staticmethod
    def release(task_id: str, lease_id: str) -> bool:
        with db.transaction() as con:
            result = con.execute("DELETE FROM task_writer_leases WHERE task_id=? AND lease_id=? AND state='active'",
                                 (task_id, lease_id))
        return result.rowcount == 1

    @staticmethod
    def transfer(task_id: str, lease_id: str, current_run_id: str, current_agent_id: str,
                 next_run_id: str, next_agent_id: str) -> bool:
        """Transfer only after the caller has stopped and joined the previous worker process tree."""
        with db.transaction() as con:
            result = con.execute("""UPDATE task_writer_leases
                SET run_id=?,agent_id=?,updated_at=CURRENT_TIMESTAMP
                WHERE task_id=? AND lease_id=? AND run_id=? AND agent_id=? AND state='active'""",
                (next_run_id, next_agent_id, task_id, lease_id, current_run_id, current_agent_id))
        return result.rowcount == 1

    @staticmethod
    def mark_orphaned(task_id: str) -> bool:
        with db.transaction() as con:
            result = con.execute("""UPDATE task_writer_leases SET state='orphaned',updated_at=CURRENT_TIMESTAMP
                WHERE task_id=? AND state='active'""", (task_id,))
        return result.rowcount == 1

    @staticmethod
    def get(task_id: str) -> dict | None:
        with db.connect() as con:
            row = con.execute("""SELECT task_id,lease_id,run_id,agent_id,state,acquired_at,updated_at
                FROM task_writer_leases WHERE task_id=?""", (task_id,)).fetchone()
        return dict(row) if row else None
