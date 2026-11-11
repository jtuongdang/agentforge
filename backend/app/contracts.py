from __future__ import annotations

import uuid

from . import db


class ContractManager:
    def propose(self, title: str, body: str) -> dict:
        contract_id = str(uuid.uuid4())
        with db.transaction() as con:
            con.execute("INSERT INTO contracts(id,title,body) VALUES(?,?,?)", (contract_id, title, body))
            row = con.execute("SELECT * FROM contracts WHERE id=?", (contract_id,)).fetchone()
        db.event(None, "contract_proposed", f"Contract proposed: {title}", {"contract_id": contract_id})
        return dict(row)

    def list(self) -> list[dict]:
        with db.connect() as con:
            return [dict(row) for row in con.execute("SELECT * FROM contracts ORDER BY created_at DESC").fetchall()]

    def get(self, contract_id: str) -> dict | None:
        with db.connect() as con:
            row = con.execute("SELECT * FROM contracts WHERE id=?", (contract_id,)).fetchone()
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
