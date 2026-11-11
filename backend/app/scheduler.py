from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from . import db
from .engine import AgentExecutionEngine, get_task, log_event, redact, set_task
from .fleet import (cooldown_remaining, credential_id, credential_key, disk_free_bytes,
                    settings as fleet_settings)
from .git_ops import repository, run
from .validation import scopes_may_overlap


class TaskScheduler:
    """Dispatches independent ready tasks within each executor's configured concurrency limit."""

    SUCCESS_STATES = {"completed"}
    FAILURE_STATES = {"failed", "blocked", "cancelled", "missing"}

    def __init__(self, execution: AgentExecutionEngine):
        self.execution = execution
        self.registry = execution.registry
        self.active: dict[str, asyncio.Task] = {}
        self.agent_counts: dict[str, int] = {}
        self.active_scopes: dict[str, tuple[str, str, list[str]]] = {}
        self._stopping = False
        self._lock = asyncio.Lock()
        self._last_prune_time = 0

    async def start(self) -> None:
        self._stopping = False
        while not self._stopping:
            try:
                for candidate in self._queued_tasks():
                    async with self._lock:
                        if candidate["id"] in self.active:
                            continue
                        agent = self._choose_agent(candidate)
                        if agent and self._claim_task(candidate["id"], agent):
                            job = asyncio.create_task(self.execution.execute(candidate["id"], agent))
                            self.active[candidate["id"]] = job
                            self.active_scopes[candidate["id"]] = (
                                candidate["repository_id"], candidate.get("base_branch") or "",
                                candidate["file_globs"])
                            self.agent_counts[agent["id"]] = self.agent_counts.get(agent["id"], 0) + 1
                            job.add_done_callback(lambda finished, tid=candidate["id"], aid=agent["id"]:
                                                  self._finished(tid, aid, finished))
            except Exception as exc:
                log_event(None, "scheduler_error", str(exc))

            try:
                import time as _t
                if _t.time() - self._last_prune_time > 3600:
                    self._last_prune_time = _t.time()
                    # Background maintenance may report cleanup candidates, but must never delete user data.
                    asyncio.create_task(asyncio.to_thread(self.execution.worktrees.prune_old, 24.0, True, True))
                    from .storage import prune_events
                    asyncio.create_task(asyncio.to_thread(prune_events))
            except Exception:
                pass

            await asyncio.sleep(0.75)

    async def stop(self) -> None:
        self._stopping = True
        jobs = list(self.active.values())
        for job in jobs:
            job.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)

    def _finished(self, task_id: str, agent_id: str, job: asyncio.Task) -> None:
        self.active.pop(task_id, None)
        self.active_scopes.pop(task_id, None)
        self.agent_counts[agent_id] = max(0, self.agent_counts.get(agent_id, 1) - 1)
        if not job.cancelled() and job.exception() is not None:
            error = redact(str(job.exception()))
            set_task(task_id, "failed", error=error, finished_at=datetime.now(timezone.utc).isoformat())
            log_event(task_id, "failed", f"Unhandled scheduler error: {error}", agent_id=agent_id)

    @staticmethod
    def _claim_task(task_id: str, agent: dict | None = None) -> bool:
        """Atomically reserve global run, disk and credential budget across schedulers."""
        free_disk = disk_free_bytes()
        active_states = ("planning", "assigned", "running", "validating", "recovering", "publishing")
        marks = ",".join("?" for _ in active_states)
        with db.transaction() as con:
            con.execute("BEGIN IMMEDIATE")
            config = fleet_settings(con)
            active = con.execute(f"SELECT COUNT(*) FROM tasks WHERE status IN ({marks})", active_states).fetchone()[0]
            if active >= config["max_active_runs"]:
                return False
            required_free = config["min_free_disk_bytes"] + (active + 1) * config["disk_reserve_per_run_bytes"]
            if free_disk < required_free:
                return False
            if agent and agent.get("adapter") != "handoff":
                if con.execute("SELECT 1 FROM credential_cooldowns WHERE credential_key=? "
                               "AND julianday(expires_at)>julianday('now')", (credential_id(agent),)).fetchone():
                    return False
                adapter, executable = credential_key(agent)
                used = con.execute(f"""SELECT COUNT(*) FROM tasks t JOIN agents a ON a.id=t.assigned_agent_id
                    WHERE t.status IN ({marks}) AND a.adapter=? AND
                    COALESCE(json_extract(a.command,'$[0]'),a.adapter)=?""",
                    (*active_states, adapter, executable)).fetchone()[0]
                if used >= config["max_per_credential"]:
                    return False
                used_by_agent = con.execute(f"SELECT COUNT(*) FROM tasks WHERE status IN ({marks}) "
                                            "AND assigned_agent_id=?", (*active_states, agent["id"])).fetchone()[0]
                if used_by_agent >= agent.get("max_parallel", 1):
                    return False
            result = con.execute("""UPDATE tasks SET status='planning',assigned_agent_id=COALESCE(?,assigned_agent_id),
                updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('queued','waiting_for_scope')""",
                (agent["id"] if agent else None, task_id))
        return result.rowcount == 1

    def _queued_tasks(self) -> list[dict]:
        with db.connect() as con:
            rows = con.execute("""SELECT id FROM tasks WHERE status IN ('queued','waiting_for_scope')
                ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
                created_at,id""").fetchall()
        return [get_task(row["id"]) for row in rows]

    def _choose_agent(self, task: dict) -> dict | None:
        conflict = self._scope_conflict(task)
        if conflict:
            if task["status"] != "waiting_for_scope":
                set_task(task["id"], "waiting_for_scope")
                log_event(task["id"], "scope_wait", f"Waiting for task {conflict} to release an overlapping file scope.")
            return None
        if task["status"] == "waiting_for_scope":
            set_task(task["id"], "queued")
            log_event(task["id"], "scope_released", "The overlapping task finished; this task returned to the scheduling queue.")
        dependency_states = self._dependency_states(task["dependencies"])
        if any(state in self.FAILURE_STATES for state in dependency_states):
            set_task(task["id"], "blocked", error="A dependency did not complete successfully.")
            log_event(task["id"], "dependency_blocked", "Task blocked because a dependency failed or was cancelled.")
            return None
        if any(state not in self.SUCCESS_STATES for state in dependency_states):
            return None
        if task["dependencies"] and not self._dependencies_integrated(task):
            return None
        if task["dependencies"] and not task["contract_id"]:
            set_task(task["id"], "waiting_for_contract")
            log_event(task["id"], "contract_required", "Dependent tasks require an approved interface contract.")
            return None
        if task["contract_id"]:
            with db.connect() as con:
                contract = con.execute("SELECT state FROM contracts WHERE id=?", (task["contract_id"],)).fetchone()
            if contract is None or contract["state"] != "approved":
                set_task(task["id"], "waiting_for_contract")
                return None
        candidates = [agent for agent in self.registry.list()
                      if agent["enabled"]
                      and (agent["adapter"] != "provider" or agent["id"] == task.get("preferred_agent_id"))
                      and set(task["required_capabilities"]).issubset(agent["capabilities"])]
        if task["preferred_agent_id"]:
            candidates.sort(key=lambda agent: agent["id"] != task["preferred_agent_id"])
        for agent in candidates:
            if cooldown_remaining(agent) > 0:
                continue
            if agent["adapter"] == "handoff":
                return agent
            if agent["available"] and self.agent_counts.get(agent["id"], 0) < agent["max_parallel"]:
                return agent
        return None

    @staticmethod
    def _dependencies_integrated(task: dict) -> bool:
        repo = repository(task["repository_id"])
        base_branch = task.get("base_branch") or repo["default_branch"]
        for dependency_id in task["dependencies"]:
            dependency = get_task(dependency_id)
            if not dependency or dependency["repository_id"] != task["repository_id"]:
                return False
            commit = (dependency.get("validation") or {}).get("commit")
            if not commit:
                return False
            integrated = run(["git", "merge-base", "--is-ancestor", commit, base_branch], repo["path"])
            if integrated.returncode != 0:
                return False
        return True

    def _scope_conflict(self, task: dict) -> str | None:
        base_branch = task.get("base_branch") or repository(task["repository_id"])["default_branch"]
        for task_id, (repository_id, active_base, file_globs) in self.active_scopes.items():
            if (task_id != task["id"] and repository_id == task["repository_id"]
                    and active_base == base_branch
                    and scopes_may_overlap(task["file_globs"], file_globs)):
                return task_id
        with db.connect() as con:
            rows = con.execute("""SELECT t.id,t.file_globs,
                    COALESCE(t.base_branch,r.default_branch) AS base_branch
                FROM tasks t JOIN repositories r ON r.id=t.repository_id
                WHERE t.repository_id=? AND t.id<>? AND t.status IN
                ('planning','assigned','running','validating','recovering','handoff_ready','awaiting_pr_approval','publishing')
                ORDER BY t.created_at,t.id""", (task["repository_id"], task["id"])).fetchall()
        for row in rows:
            if (row["base_branch"] == base_branch
                    and scopes_may_overlap(task["file_globs"], db.json.loads(row["file_globs"]))):
                return row["id"]
        return None

    @staticmethod
    def _dependency_states(dependencies: list[str]) -> list[str]:
        if not dependencies:
            return []
        marks = ",".join("?" for _ in dependencies)
        with db.connect() as con:
            rows = con.execute(f"SELECT id,status FROM tasks WHERE id IN ({marks})", dependencies).fetchall()
        status_by_id = {row["id"]: row["status"] for row in rows}
        return [status_by_id.get(dependency, "missing") for dependency in dependencies]
