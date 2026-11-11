from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import db
from . import code_context
from . import provider_worker
from .git_ops import repository
from .fleet import credential_key, set_credential_cooldown, transfer_task_to_rescue
from .recovery import RecoveryManager
from .validation import ValidationEngine
from .worktrees import GitWorktreeManager
from .planner import TaskPlanner
from .project_guidance import load_project_guidance
from .processes import create_managed_process, process_group_options, terminate_process_tree
from .registry import AgentRegistry
from .writer_leases import WriterLeaseManager

REDACTIONS = [
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)(\s*[=:]\s*)[^\s'\"]{8,}"), r"\1\2[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
]


def redact(value: str) -> str:
    for pattern, replacement in REDACTIONS:
        value = pattern.sub(replacement, value)
    return value[:4000]


def set_task(task_id: str, status: str, **values) -> None:
    allowed = {"assigned_agent_id", "branch", "worktree", "retries", "validation", "pr_url", "error", "finished_at"}
    pairs = [(k, db.dumps(v) if k == "validation" else v) for k, v in values.items() if k in allowed]
    sets = ["status=?", "updated_at=CURRENT_TIMESTAMP"] + [f"{k}=?" for k, _ in pairs]
    with db.transaction() as con:
        con.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", [status, *[v for _, v in pairs], task_id])


def get_task(task_id: str) -> dict | None:
    with db.connect() as con:
        row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        return None
    task = dict(row)
    for key in ("required_capabilities", "file_globs", "dependencies", "validation", "acceptance_criteria"):
        task[key] = json.loads(task[key])
    for key in ("pr_approved", "provider_data_consent"):
        task[key] = bool(task[key])
    return task


def log_event(task_id: str | None, kind: str, message: str, details: dict | None = None,
              agent_id: str | None = None) -> None:
    db.event(task_id, kind, redact(message), details or {}, agent_id)


class AgentExecutionEngine:
    def __init__(self, registry: AgentRegistry, worktrees: GitWorktreeManager | None = None,
                 validation: ValidationEngine | None = None, recovery: RecoveryManager | None = None,
                 build_slots=None):
        self.registry = registry
        self.planner = TaskPlanner()
        self.worktrees = worktrees or GitWorktreeManager()
        self.validation = validation or ValidationEngine()
        self.recovery = recovery or RecoveryManager()
        self.build_slots = build_slots

    async def execute(self, task_id: str, agent: dict) -> None:
        task = get_task(task_id)
        if task is None:
            return
        repo = repository(task["repository_id"])
        task["base_branch"] = task.get("base_branch") or repo["default_branch"]
        set_task(task_id, "planning", assigned_agent_id=agent["id"])
        log_event(task_id, "planning", f"Planner prepared bounded task for {agent['name']}.", agent_id=agent["id"])
        try:
            branch, worktree = await asyncio.to_thread(
                self.worktrees.prepare, repo, task_id, task.get("base_sha") or task["base_branch"], task["title"])
            query = f'{task["title"]} {task["objective"]}'
            guidance, guidance_files = load_project_guidance(
                worktree, task["file_globs"], task["repository_id"], query, include_rag=False)
            try:
                context_result = await asyncio.to_thread(
                    code_context.search, task["repository_id"], worktree, query,
                    max_chars=code_context.MAX_CONTEXT_CHARS,
                    exclude_paths={name for name in guidance_files if not name.startswith("Orchestrator baseline:")})
                if context_result["context"]:
                    guidance += "\n\n" + context_result["context"]
                    guidance_files.extend(
                        f"{hit['path']}:{hit['start_line']}-{hit['end_line']} @ {hit['revision'][:12]} (source context)"
                        for hit in context_result["citations"])
                if context_result["stats"]["parse_errors"] or context_result["stats"]["truncated"]:
                    log_event(task_id, "code_context_partial",
                              "Source context is partial because some files could not be parsed or indexing limits were reached.",
                              {"revision": context_result["revision"], "parse_errors": context_result["stats"]["parse_errors"],
                               "truncated": context_result["stats"]["truncated"]},
                              agent_id=agent["id"])
            except Exception as exc:
                context_result = None
                log_event(task_id, "code_context_unavailable",
                          "Revision-pinned source context could not be indexed; continuing with repository guidance only.",
                          {"error_type": type(exc).__name__}, agent_id=agent["id"])
            plan = self.planner.plan(task["title"], task["objective"], task["file_globs"],
                                     task["required_capabilities"], task.get("acceptance_criteria", []),
                                     guidance, guidance_files)
            context_details = ({"revision": context_result["revision"], "citations": context_result["citations"],
                                "indexed": context_result["stats"]["indexed"],
                                "unchanged": context_result["stats"]["unchanged"]}
                               if context_result else {"available": False})
            log_event(task_id, "project_guidance_loaded",
                      f"Loaded {len(guidance_files)} guidance and revision-pinned source citation(s) before Agent execution.",
                      {"files": guidance_files, "source_context": context_details}, agent_id=agent["id"])
            task_file = db.DATA_ROOT / "handoffs" / f"{task_id}.md"
            task_file.parent.mkdir(parents=True, exist_ok=True)
            task_content = self._task_markdown(task, plan, branch, worktree,
                                               include_worktree_path=agent.get("adapter") != "provider")
            task_file.write_text(task_content, encoding="utf-8")
            set_task(task_id, "assigned", branch=branch, worktree=worktree)
            log_event(task_id, "worktree_created", f"Branch {branch} created in an isolated worktree.", agent_id=agent["id"])
            if agent["adapter"] == "handoff":
                set_task(task_id, "handoff_ready")
                log_event(task_id, "handoff_ready", f"Open the exported task package for {agent['name']}.",
                          {"task_file": str(task_file)}, agent["id"])
                return

            validation = {}
            last_error = None
            lease_id: str | None = None
            tried_agent_ids = {agent["id"]}
            preclaimed_run_id: str | None = None

            def release_writer() -> None:
                nonlocal lease_id
                if lease_id:
                    WriterLeaseManager.release(task_id, lease_id)
                    lease_id = None

            def finish_failed_run(run_id: str, code: int, error: str, result: dict,
                                  output_tail: list[str], checkpoint: dict | None = None):
                failure = self.recovery.classify(code, output_tail, result, error)
                with db.transaction() as con:
                    con.execute("UPDATE runs SET state='failed',exit_code=?,finished_at=CURRENT_TIMESTAMP,report=? WHERE id=?",
                                (code, db.dumps({"error": error, "failure_class": failure.category,
                                                "retryable": failure.retryable, "checkpoint": checkpoint,
                                                **result}), run_id))
                return failure

            for attempt in range(task["max_retries"] + 1):
                set_task(task_id, "running", retries=attempt)
                if preclaimed_run_id:
                    run_id = preclaimed_run_id
                    preclaimed_run_id = None
                else:
                    run_id = str(uuid.uuid4())
                    with db.transaction() as con:
                        con.execute("INSERT INTO runs(id,task_id,agent_id,state) VALUES(?,?,?,'running')",
                                    (run_id, task_id, agent["id"]))
                    candidate_lease = str(uuid.uuid4())
                    if not WriterLeaseManager.acquire(task_id, run_id, agent["id"], candidate_lease):
                        message = "Task worktree already has an active or orphaned writer lease; no worker was started."
                        with db.transaction() as con:
                            con.execute("UPDATE runs SET state='blocked',exit_code=125,finished_at=CURRENT_TIMESTAMP,report=? WHERE id=?",
                                        (db.dumps({"error": message, "failure_class": "writer_lease_conflict"}), run_id))
                        validation = {"passed": False, "blocked": True, "reason": "writer_lease_conflict"}
                        last_error = message
                        log_event(task_id, "writer_lease_conflict", message, agent_id=agent["id"])
                        break
                    lease_id = candidate_lease
                prompt = task_content
                if last_error:
                    prompt += f"\n\nRepair attempt {attempt}: previous validation failed:\n{last_error}\nFix this and re-run the relevant checks."
                output_tail: list[str] = []
                def remember_output(line: str) -> None:
                    output_tail.append(redact(line)[:1000])
                    del output_tail[:-40]
                code = await self._invoke(agent, worktree, task_file, prompt, task_id, task["file_globs"],
                                          on_output=remember_output)
                checkpoint = None
                if code != 0:
                    last_error = f"Agent process exited with status {code}."
                    paths = await asyncio.to_thread(self.worktrees.changed_paths, worktree,
                                                    task.get("base_sha") or task.get("base_branch"))
                    outside = self.validation.inspect_scope(paths, task["file_globs"], agent["file_globs"])
                    secret_files = self.validation.inspect_secrets(worktree, paths)
                    checkpoint = {"retained_worktree": worktree,
                                  "base_ref": task.get("base_sha") or task.get("base_branch"),
                                  "changed_paths": paths, "scanned": True}
                    if outside or secret_files:
                        reason = "out_of_scope" if outside else "possible_secret"
                        blocked_paths = outside or secret_files
                        validation = {"passed": False, "blocked": True, "reason": reason, "paths": blocked_paths}
                        last_error = (f"Changes outside declared file scope: {', '.join(outside)}" if outside else
                                      f"Possible secret content in changed files: {', '.join(secret_files)}")
                        finish_failed_run(run_id, code, last_error, validation, output_tail, checkpoint)
                        release_writer()
                        break
                else:
                    paths = await asyncio.to_thread(self.worktrees.changed_paths, worktree,
                                                    task.get("base_sha") or task.get("base_branch"))
                    checkpoint = {"retained_worktree": worktree,
                                  "base_ref": task.get("base_sha") or task.get("base_branch"),
                                  "changed_paths": paths, "scanned": True}
                    outside = self.validation.inspect_scope(paths, task["file_globs"], agent["file_globs"])
                    secret_files = self.validation.inspect_secrets(worktree, paths)
                    if outside:
                        validation = {"passed": False, "blocked": True, "reason": "out_of_scope",
                                      "paths": outside}
                        last_error = f"Changes outside declared file scope: {', '.join(validation['paths'])}"
                        finish_failed_run(run_id, code, last_error, validation, output_tail, checkpoint)
                        release_writer()
                        break
                    if secret_files:
                        validation = {"passed": False, "blocked": True, "reason": "possible_secret", "paths": secret_files}
                        last_error = f"Possible secret content in changed files: {', '.join(secret_files)}"
                        finish_failed_run(run_id, code, last_error, validation, output_tail, checkpoint)
                        release_writer()
                        break
                    set_task(task_id, "validating")
                    if self.build_slots:
                        await self.build_slots.acquire()
                    try:
                        validation = await self.validation.validate(
                            worktree, lambda kind, msg: log_event(task_id, kind, msg, agent_id=agent["id"]),
                            repo.get("validation_profile", "auto"), repo.get("validation_command", []),
                            paths=paths)
                    finally:
                        if self.build_slots:
                            self.build_slots.release()
                    if validation["passed"]:
                        sha = await asyncio.to_thread(self.worktrees.commit, worktree, paths, task_id, task["title"],
                                                      task.get("base_sha") or task.get("base_branch"))
                        validation.update({"changed_paths": paths, "commit": sha, "scope_passed": True})
                        set_task(task_id, "awaiting_pr_approval", validation=validation, error=None)
                        log_event(task_id, "awaiting_pr_approval", "Validation passed and task branch committed; push/Draft PR requires approval.",
                                  validation, agent["id"])
                        with db.transaction() as con:
                            con.execute("UPDATE runs SET state='passed',exit_code=0,finished_at=CURRENT_TIMESTAMP,report=? WHERE id=?",
                                        (db.dumps(validation), run_id))
                        release_writer()
                        return
                    last_error = validation.get("summary", "Validation failed")
                    if validation.get("blocked"):
                        finish_failed_run(run_id, code, last_error, validation, output_tail, checkpoint)
                        release_writer()
                        break
                failure = finish_failed_run(run_id, code, last_error or "", validation, output_tail, checkpoint)
                retry_budget = self.recovery.should_retry(attempt, task["max_retries"], bool(validation.get("blocked")))
                can_recover = failure.retryable or failure.category == "auth"
                rescue_category = failure.category in {"auth", "rate_limit", "network", "timeout"}
                if retry_budget and can_recover and lease_id:
                    if failure.category == "rate_limit":
                        set_credential_cooldown(agent,
                                                failure.retry_after_seconds if failure.retry_after_seconds is not None else 30.0,
                                                failure.category)
                    required = set(task["required_capabilities"])
                    current_credential = credential_key(agent)
                    rescue_candidates = [candidate for candidate in self.registry.list() if rescue_category
                        if candidate["id"] not in tried_agent_ids and candidate["enabled"] and candidate["available"]
                        and candidate["adapter"] != "handoff" and set(candidate["capabilities"]).issuperset(required)
                        and credential_key(candidate) != current_credential
                        and not self.validation.inspect_scope(paths, task["file_globs"], candidate["file_globs"])]
                    rescue_candidates.sort(key=lambda item: (item["adapter"] == agent["adapter"], item["name"].lower()))
                    for candidate in rescue_candidates:
                        next_run_id = str(uuid.uuid4())
                        if transfer_task_to_rescue(task_id, agent["id"], candidate, lease_id, run_id, next_run_id):
                            previous_agent = agent
                            agent = candidate
                            tried_agent_ids.add(candidate["id"])
                            preclaimed_run_id = next_run_id
                            set_task(task_id, "recovering", retries=attempt + 1, error=last_error)
                            log_event(task_id, "worker_rescue_transfer",
                                      f"Stopped {previous_agent['name']} and transferred the task writer lease to {agent['name']}.",
                                      {"failure_class": failure.category, "from_agent_id": previous_agent["id"],
                                       "to_agent_id": agent["id"], "from_run_id": run_id,
                                       "to_run_id": next_run_id, "checkpoint": checkpoint}, agent_id=agent["id"])
                            break
                if not preclaimed_run_id:
                    release_writer()
                if retry_budget and can_recover and not preclaimed_run_id:
                    retry_after = failure.retry_after_seconds
                    if failure.category == "rate_limit" and retry_after is None:
                        retry_after = 30.0
                    delay = self.recovery.backoff_seconds(attempt, retry_after)
                    set_task(task_id, "recovering", retries=attempt + 1, error=last_error)
                    log_event(task_id, "recovering",
                              f"Bounded {failure.category} recovery retry {attempt + 1}/{task['max_retries']} "
                              f"after {delay:g}s: {last_error}",
                              {"failure_class": failure.category, "retry_after_seconds": failure.retry_after_seconds,
                               "backoff_seconds": delay}, agent_id=agent["id"])
                    await asyncio.sleep(delay)
                else:
                    if not preclaimed_run_id:
                        break
            final_status = "blocked" if validation.get("blocked") else "failed"
            set_task(task_id, final_status, error=last_error, validation=validation,
                     finished_at=datetime.now(timezone.utc).isoformat())
            log_event(task_id, final_status, last_error or "Task failed.", validation, agent["id"])
        except asyncio.CancelledError:
            if 'release_writer' in locals():
                release_writer()
            set_task(task_id, "cancelled", error="Stopped by operator; worktree retained for review.",
                     finished_at=datetime.now(timezone.utc).isoformat())
            raise
        except Exception as exc:
            if 'release_writer' in locals():
                release_writer()
            message = redact(str(exc))
            set_task(task_id, "failed", error=message, finished_at=datetime.now(timezone.utc).isoformat())
            log_event(task_id, "failed", message, agent_id=agent["id"])

    async def _invoke(self, agent: dict, worktree: str, task_file: Path, prompt: str, task_id: str,
                      file_globs: list[str] | None = None, on_output=None) -> int:
        command = agent["command"]
        if agent["adapter"] == "provider":
            if not command:
                raise RuntimeError("Provider worker has no linked connection")
            current_task = get_task(task_id)
            if not current_task:
                raise RuntimeError("Provider worker task no longer exists")
            if agent.get("provider") != "ollama" and not current_task.get("provider_data_consent"):
                raise RuntimeError("Cloud provider execution requires explicit task data-transfer consent")

            def provider_tool_event(name: str, path: str | None, written: bool) -> None:
                label = f" {path}" if path else ""
                action = "wrote" if written else "called"
                message = f"Provider worker {action} {name}{label}."
                if on_output:
                    on_output(message)
                log_event(task_id, "provider_tool", message,
                          {"tool": name, "path": path, "written": written}, agent_id=agent["id"])

            return await provider_worker.execute(command[0], prompt, worktree, file_globs or [],
                                                 agent.get("file_globs") or [], on_tool=provider_tool_event)
        if agent["adapter"] == "codex":
            executable = command[0] if command else shutil.which("codex")
            args = [executable, "exec", "--json", "--color", "never", "--cd", worktree,
                    "--sandbox", "workspace-write", "--ephemeral", "-"]
            stdin_text = prompt
        elif agent["adapter"] == "copilot":
            executable = command[0] if command else shutil.which("copilot")
            if not executable:
                raise RuntimeError("GitHub Copilot CLI is not installed")
            args = [executable, "--prompt", prompt, "--silent", "--output-format", "json",
                    "--disable-builtin-mcps", "--disable-client-telemetry", "--allow-all-tools"]
            stdin_text = None
        elif agent["adapter"] == "antigravity":
            executable = command[0] if command else shutil.which("agy")
            if not executable:
                default_agy = Path.home() / ".local" / "bin" / "agy"
                executable = str(default_agy) if default_agy.is_file() else None
            if not executable:
                raise RuntimeError("Google Antigravity CLI is not installed")
            model = "gemini-3.8-flash-medium"
            if command and "--model" in command:
                idx = command.index("--model")
                if idx + 1 < len(command):
                    model = command[idx + 1]
            if not file_globs:
                raise RuntimeError("Antigravity execution requires a non-empty task file scope")
            worktree_root = Path(worktree).resolve(strict=True)
            project_guidance = Path(__file__).resolve().parents[2] / "docs" / "PROJECT_CONVENTIONS.md"
            agent_prompt = (
                prompt
                + "\n\nAntigravity file access instructions: `read_file` requires absolute paths. "
                + f"Use absolute paths only under the isolated task worktree {worktree_root.as_posix()}; "
                + f"the Orchestrator guidance file is also readable at {project_guidance.as_posix()}. "
                + "Write only the task's declared output files in the isolated worktree."
            )
            settings = {
                "permissions": {
                    "allow": [
                        f"read_file({worktree_root.as_posix()}/**)",
                        f"read_file({project_guidance.as_posix()})",
                        *self._antigravity_write_permissions(worktree, file_globs),
                    ],
                    "deny": ["mcp(*)", "read_url(*)", "execute_url(*)", "command(*)"],
                }
            }
            args = [executable, "--print", agent_prompt, "--output-format", "stream-json",
                    "--mode=accept-edits", "--model", model,
                    "--sandbox", "--settings", json.dumps(settings),
                    "--print-timeout", "60m"]
            stdin_text = None
        elif agent["adapter"] == "exec":
            replacements = {"{worktree}": worktree, "{task_file}": str(task_file)}
            args = []
            for token in command:
                for marker, value in replacements.items():
                    token = token.replace(marker, value)
                args.append(token)
            stdin_text = prompt
        else:
            raise RuntimeError("The handoff adapter cannot execute as a local CLI")
        log_event(task_id, "agent_started", f"Starting adapter {agent['adapter']}.", agent_id=agent["id"])
        try:
            proc = await create_managed_process(*args, cwd=worktree,
                stdin=asyncio.subprocess.PIPE if stdin_text else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                limit=8 * 1024 * 1024, **process_group_options())
            assert proc.stdout is not None
            if stdin_text and proc.stdin:
                proc.stdin.write(stdin_text.encode())
                await proc.stdin.drain()
                proc.stdin.close()
            async def stream_output():
                while line := await proc.stdout.readline():
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded:
                        if on_output:
                            on_output(decoded)
                        log_event(task_id, "agent_log", self._event_summary(decoded), agent_id=agent["id"])
            reader_task = asyncio.create_task(stream_output())
            try:
                await asyncio.wait_for(proc.wait(), timeout=3600)
            except asyncio.TimeoutError:
                await terminate_process_tree(proc)
                reader_task.cancel()
                await asyncio.gather(reader_task, return_exceptions=True)
                log_event(task_id, "agent_timeout", "Agent process exceeded 60 minute limit.", agent_id=agent["id"])
                return 124
            except asyncio.CancelledError:
                await terminate_process_tree(proc)
                reader_task.cancel()
                await asyncio.gather(reader_task, return_exceptions=True)
                raise
            code = proc.returncode or 0
            # A CLI may exit while a descendant keeps running in its process group.
            # Join/terminate that group before releasing this worktree's writer lease.
            await terminate_process_tree(proc, grace_seconds=0.25)
            try:
                await asyncio.wait_for(reader_task, timeout=5)
            except asyncio.TimeoutError:
                reader_task.cancel()
                await asyncio.gather(reader_task, return_exceptions=True)
            return code
        except asyncio.CancelledError:
            if "proc" in locals() and proc.returncode is None:
                await terminate_process_tree(proc)
            if "reader_task" in locals() and not reader_task.done():
                reader_task.cancel()
                await asyncio.gather(reader_task, return_exceptions=True)
            raise
        except FileNotFoundError:
            raise RuntimeError(f"Configured agent executable not found: {args[0]}")
        except Exception:
            if "proc" in locals():
                await terminate_process_tree(proc)
            if "reader_task" in locals() and not reader_task.done():
                reader_task.cancel()
                await asyncio.gather(reader_task, return_exceptions=True)
            raise

    @staticmethod
    def _antigravity_write_permissions(worktree: str, file_globs: list[str]) -> list[str]:
        root = Path(worktree).resolve(strict=True)
        permissions = []
        for pattern in file_globs:
            normalized = pattern.replace("\\", "/").strip()
            parts = normalized.split("/")
            if not normalized or normalized.startswith("/") or ".." in parts or ".git" in parts:
                raise RuntimeError("Antigravity task scope contains an unsafe file glob")
            target = (root / normalized).resolve(strict=False)
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise RuntimeError("Antigravity task scope resolves outside its worktree") from exc
            if target == root:
                raise RuntimeError("Antigravity task scope cannot grant access to the worktree root")
            permissions.append(f"write_file({target.as_posix()})")
        return sorted(set(permissions))

    @staticmethod
    def _event_summary(line: str) -> str:
        try:
            data = json.loads(line)
            kind = data.get("type") or data.get("event") or "agent_output"
            step = data.get("step_update")
            if kind == "step_update" and isinstance(step, dict) and step.get("step_type") == "tool":
                info = step.get("tool_info") if isinstance(step.get("tool_info"), dict) else {}
                name = step.get("tool_name") or info.get("name") or "tool"
                parameters = info.get("parameters") if isinstance(info.get("parameters"), dict) else {}
                target = parameters.get("Path") if name == "read_file" else None
                error = info.get("error") if isinstance(info.get("error"), dict) else {}
                detail = f" {target}" if target else ""
                if error.get("message"):
                    detail += f": {error['message']}"
                return redact(f"tool {name}{detail}")
            item = data.get("item", {})
            message = item.get("text") if isinstance(item, dict) else None
            message = message or data.get("message") or data.get("text") or kind
            return redact(f"{kind}: {message}")
        except json.JSONDecodeError:
            return redact(line)

    @staticmethod
    def _task_markdown(task: dict, plan, branch: str, worktree: str,
                       include_worktree_path: bool = True) -> str:
        contract = ""
        if task["contract_id"]:
            with db.connect() as con:
                row = con.execute("SELECT title,body,version FROM contracts WHERE id=?", (task["contract_id"],)).fetchone()
            if row:
                contract = f"\n## Approved contract v{row['version']}: {row['title']}\n{row['body']}\n"
        worktree_value = worktree if include_worktree_path else "(local path withheld)"
        return (f"# {plan.title}\n\n{plan.objective}\n"
                f"\n## Isolation\nBase branch: `{task.get('base_branch') or 'repository default'}`\n"
                f"Task branch: `{branch}`\nWorktree: `{worktree_value}`\n"
                f"Pinned base commit: `{task.get('base_sha') or 'not recorded'}`\n"
                f"\n## Allowed files\n" + "\n".join(f"- `{p}`" for p in plan.file_globs) +
                f"\n## Contract\n{contract or 'No shared contract declared.'}\n"
                "## Constraints\n- Use synthetic or appropriately anonymized test data; never copy real personal or confidential data.\n"
                "- Do not push, merge, force-push, delete branches, or connect to production databases or systems.\n"
                "- Do not edit outside the allowed file globs.\n"
                "- Report changed files, validation commands and any blockers.\n")

