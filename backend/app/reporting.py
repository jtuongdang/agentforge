from __future__ import annotations

import json

from . import db


class ReportingManager:
    def tasks(self, limit: int = 100) -> list[dict]:
        with db.connect() as con:
            rows = con.execute("""SELECT t.*,r.name repository_name,r.remote repository_remote,
                r.default_branch repository_default_branch,r.validation_profile repository_validation_profile,
                r.validation_command repository_validation_command,a.name agent_name,
                wl.state writer_lease_state,wl.run_id writer_lease_run_id,wl.agent_id writer_lease_agent_id FROM tasks t
                JOIN repositories r ON r.id=t.repository_id LEFT JOIN agents a ON a.id=t.assigned_agent_id
                LEFT JOIN task_writer_leases wl ON wl.task_id=t.id
                ORDER BY t.created_at DESC LIMIT ?""", (max(1, min(500, limit)),)).fetchall()
        return [self._decode(dict(row)) for row in rows]

    def history(self, limit: int = 100) -> list[dict]:
        with db.connect() as con:
            rows = con.execute("""SELECT t.id,t.title,t.status,t.created_at,t.finished_at,t.branch,t.pr_url,
                a.name agent_name,r.name repository_name FROM tasks t JOIN repositories r ON r.id=t.repository_id
                LEFT JOIN agents a ON a.id=t.assigned_agent_id
                WHERE t.status IN ('completed','draft_pr','failed','blocked','cancelled')
                ORDER BY t.updated_at DESC LIMIT ?""", (max(1, min(500, limit)),)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def events(task_id: str, after: int = 0) -> list[dict]:
        with db.connect() as con:
            rows = con.execute("SELECT * FROM events WHERE task_id=? AND id>? ORDER BY id LIMIT 1000", (task_id, after)).fetchall()
        result = [dict(row) for row in rows]
        for row in result:
            row["details"] = json.loads(row["details"])
        return result

    @staticmethod
    def _decode(task: dict) -> dict:
        for key in ("required_capabilities", "file_globs", "dependencies", "validation", "acceptance_criteria",
                    "repository_validation_command"):
            task[key] = json.loads(task[key])
        task["base_branch"] = task.get("base_branch") or task.get("repository_default_branch")
        task["pr_approved"] = bool(task["pr_approved"])
        task["provider_data_consent"] = bool(task.get("provider_data_consent", 0))
        return task
