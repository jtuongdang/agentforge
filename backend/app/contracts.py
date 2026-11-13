from __future__ import annotations

import uuid

from . import db


class ContractManager:
    def propose(self, title: str, body: str, repository_id: str | None = None) -> dict:
        contract_id = str(uuid.uuid4())
        repo_id = repository_id.strip() if isinstance(repository_id, str) and repository_id.strip() else None
        with db.transaction() as con:
            con.execute(
                "INSERT INTO contracts(id,title,body,repository_id) VALUES(?,?,?,?)",
                (contract_id, title, body, repo_id),
            )
            row = con.execute(
                "SELECT c.*, r.name AS repository_name FROM contracts c "
                "LEFT JOIN repositories r ON c.repository_id = r.id WHERE c.id=?",
                (contract_id,),
            ).fetchone()
        db.event(None, "contract_proposed", f"Contract proposed: {title}", {"contract_id": contract_id, "repository_id": repo_id})
        return dict(row)

    def list(self, repository_id: str | None = None) -> list[dict]:
        with db.connect() as con:
            if repository_id:
                rows = con.execute(
                    "SELECT c.*, r.name AS repository_name FROM contracts c "
                    "LEFT JOIN repositories r ON c.repository_id = r.id "
                    "WHERE c.repository_id = ? OR c.repository_id IS NULL ORDER BY c.created_at DESC",
                    (repository_id,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT c.*, r.name AS repository_name FROM contracts c "
                    "LEFT JOIN repositories r ON c.repository_id = r.id "
                    "ORDER BY c.created_at DESC"
                ).fetchall()
            return [dict(row) for row in rows]

    def get(self, contract_id: str) -> dict | None:
        with db.connect() as con:
            row = con.execute(
                "SELECT c.*, r.name AS repository_name FROM contracts c "
                "LEFT JOIN repositories r ON c.repository_id = r.id WHERE c.id=?",
                (contract_id,),
            ).fetchone()
        return dict(row) if row else None

    def approve(self, contract_id: str) -> bool:
        with db.transaction() as con:
            updated = con.execute("UPDATE contracts SET state='approved',approved_at=CURRENT_TIMESTAMP WHERE id=? AND state='proposed'",
                                  (contract_id,)).rowcount
            if updated:
                con.execute("UPDATE tasks SET status='queued',updated_at=CURRENT_TIMESTAMP WHERE contract_id=? AND status='waiting_for_contract'",
                            (contract_id,))
        if updated:
            db.event(None, "contract_approved", "Shared interface contract approved", {"contract_id": contract_id})
        return bool(updated)
