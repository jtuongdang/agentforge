from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import db


class AgentRegistry:
    def refresh(self) -> list[dict]:
        agents: list[dict] = []
        codex = shutil.which("codex")
        if codex:
            version = self._version([codex, "--version"])
            auth = self._auth_status(codex)
            agents.append({
                "id": "codex-local", "name": "Codex CLI", "adapter": "codex", "command": [codex],
                "capabilities": ["analysis", "implementation", "documentation", "refactoring", "java", "python", "react", "typescript", "testing"],
                "file_globs": ["**/*"], "max_parallel": 2, "available": auth[0],
                "availability_note": f"{version}; {auth[1]}" if version else auth[1],
            })
        copilot_cli = shutil.which("copilot")
        if copilot_cli:
            version = self._version([copilot_cli, "--version"])
            agents.append({
                "id": "copilot-cli", "name": "GitHub Copilot CLI", "adapter": "copilot", "command": [copilot_cli],
                "capabilities": ["analysis", "implementation", "documentation", "refactoring", "java", "python", "react", "typescript", "testing"],
                "file_globs": ["**/*"], "max_parallel": 1, "available": False,
                "availability_note": f"{version}; installed; authentication and worker permission policy need verification",
            })
        antigravity_cli = shutil.which("agy")
        if not antigravity_cli:
            default_agy = Path.home() / ".local" / "bin" / "agy"
            if default_agy.is_file():
                antigravity_cli = str(default_agy)
        if antigravity_cli:
            version = self._version([antigravity_cli, "--version"])
            agents.append({
                "id": "antigravity-cli", "name": "Google Antigravity CLI", "adapter": "antigravity", "command": [antigravity_cli],
                "capabilities": ["analysis", "implementation", "documentation", "refactoring", "java", "python", "react", "typescript", "testing"],
                "file_globs": ["**/*"], "max_parallel": 1, "available": False,
                "availability_note": f"Antigravity CLI {version}; installed; credential check required",
            })
        # IDE adapters are present as dispatch targets only when the local app/plugin is observed.
        antigravity = Path("/Applications/Antigravity.app").exists()
        if antigravity:
            agents.append({
                "id": "antigravity-ide", "name": "Antigravity IDE (handoff)", "adapter": "handoff", "command": [],
                "capabilities": ["analysis", "implementation", "documentation", "java", "ui"],
                "file_globs": ["**/*"], "max_parallel": 1, "available": False,
                "availability_note": "App found; use this entry for manual IDE handoff. Automated tasks use the Antigravity CLI adapter.",
            })
        copilot = Path.home() / "Library/Application Support/JetBrains/IntelliJIdea2026.2/plugins/github-copilot-intellij"
        if copilot.exists() and not copilot_cli:
            agents.append({
                "id": "copilot-ide", "name": "GitHub Copilot IntelliJ (handoff)", "adapter": "handoff", "command": [],
                "capabilities": ["analysis", "implementation", "documentation", "java", "ui", "testing"],
                "file_globs": ["**/*"], "max_parallel": 1, "available": False,
                "availability_note": "IntelliJ plugin found; no standalone Copilot task CLI detected.",
            })
        with db.transaction() as con:
            for agent in agents:
                con.execute("""INSERT INTO agents(id,name,adapter,command,capabilities,file_globs,max_parallel,enabled,available,availability_note,last_seen)
                    VALUES(?,?,?,?,?,?,?,1,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,adapter=excluded.adapter,command=excluded.command,
                    capabilities=excluded.capabilities,file_globs=excluded.file_globs,max_parallel=excluded.max_parallel,
                    available=CASE WHEN agents.command=excluded.command AND excluded.adapter='antigravity'
                        AND agents.credential_status='valid' THEN 1 ELSE excluded.available END,
                    credential_status=CASE WHEN agents.command=excluded.command THEN agents.credential_status ELSE 'unchecked' END,
                    availability_note=excluded.availability_note,last_seen=CURRENT_TIMESTAMP""",
                    (agent["id"], agent["name"], agent["adapter"], db.dumps(agent["command"]),
                     db.dumps(agent["capabilities"]), db.dumps(agent["file_globs"]), agent["max_parallel"],
                     int(agent["available"]), agent["availability_note"]))
            known = {"codex-local", "copilot-cli", "antigravity-cli", "antigravity-ide", "copilot-ide"}
            seen = {agent["id"] for agent in agents}
            for stale_id in known - seen:
                con.execute("UPDATE agents SET available=0,availability_note='Executable or app was not detected during the latest refresh',last_seen=CURRENT_TIMESTAMP WHERE id=?",
                            (stale_id,))
            # Don't erase user-registered agents; executability is checked when scheduled.
        return self.list()

    @staticmethod
    def _version(command: list[str]) -> str:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=4, check=False)
            return (result.stdout or result.stderr).strip()[:200]
        except (OSError, subprocess.TimeoutExpired):
            return "CLI detected; version probe failed"

    @staticmethod
    def _auth_status(executable: str) -> tuple[bool, str]:
        try:
            result = subprocess.run([executable, "login", "status"], capture_output=True, text=True, timeout=8, check=False)
            return result.returncode == 0, "CLI reports signed in" if result.returncode == 0 else "CLI sign-in required"
        except (OSError, subprocess.TimeoutExpired):
            return False, "CLI authentication check timed out or failed"

    def list(self) -> list[dict]:
        with db.connect() as con:
            rows = con.execute("SELECT * FROM agents ORDER BY name").fetchall()
        return [self._decode(dict(r)) for r in rows]

    def get(self, agent_id: str) -> dict | None:
        with db.connect() as con:
            row = con.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        return self._decode(dict(row)) if row else None

    def set_enabled(self, agent_id: str, enabled: bool) -> dict | None:
        with db.transaction() as con:
            cursor = con.execute("UPDATE agents SET enabled=? WHERE id=?", (int(enabled), agent_id))
        return self.get(agent_id) if cursor.rowcount else None

    def check_token(self, agent_id: str) -> dict | None:
        agent = self.get(agent_id)
        if agent is None:
            return None
        status, note, usage = self._probe_credentials(agent)
        with db.transaction() as con:
            con.execute("""UPDATE agents SET credential_status=?, credential_checked_at=?,
                credential_note=?, credential_usage=?,
                available=CASE WHEN adapter IN ('codex','antigravity') THEN ?
                    WHEN adapter='copilot' THEN 0 ELSE available END WHERE id=?""",
                (status, datetime.now(timezone.utc).isoformat(timespec="seconds"), note[:300], db.dumps(usage),
                 int(status == "valid"), agent_id))
        return self.get(agent_id)

    @classmethod
    def _probe_credentials(cls, agent: dict) -> tuple[str, str, dict]:
        adapter = agent["adapter"]
        command = agent.get("command") or []
        if adapter == "handoff":
            return "not_applicable", "IDE handoff has no locally inspectable CLI credential.", {}
        if not command:
            return "unsupported", "No executable is configured for this agent.", {}
        executable = command[0]
        if adapter == "codex":
            return cls._run_probe([executable, "login", "status"], "Codex login status")
        if adapter == "copilot":
            status, note, _ = cls._run_probe([
                executable, "--prompt", "Reply exactly TOKEN_CHECK_OK. Do not use tools.",
                "--silent", "--output-format", "text", "--disable-builtin-mcps", "--disable-client-telemetry",
            ], "Copilot CLI")
            return status, "This check sends one short prompt and consumes one Copilot request. " + note, {}
        if adapter == "antigravity":
            args = [executable, "--print", "/usage", "--output-format", "json", "--print-timeout", "30s",
                    "--mode=accept-edits", "--sandbox", "--settings",
                    json.dumps({"permissions": {"deny": ["mcp(*)", "url(*)", "command(*)"]}})]
            status, note, output = cls._run_probe(args, "Antigravity usage endpoint", capture_output=True)
            usage = cls._antigravity_usage(output) if status == "valid" else {}
            return status, "Credential checked without a model prompt. " + note, usage
        if adapter == "exec":
            return "unsupported", "Generic CLI adapters have no standard credential-check command.", {}
        return "unsupported", f"Credential check is not supported for adapter '{adapter}'.", {}

    @staticmethod
    def _run_probe(args: list[str], label: str, capture_output: bool = False):
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=35, check=False)
        except subprocess.TimeoutExpired:
            return "error", f"{label} timed out.", "" if capture_output else {}
        except OSError:
            return "error", f"Could not start {label}.", "" if capture_output else {}
        output = (result.stdout or result.stderr or "").strip()
        status = "valid" if result.returncode == 0 else "invalid"
        note = f"{label} succeeded." if status == "valid" else f"{label} failed (exit {result.returncode})."
        # Raw CLI output is used only in-memory to extract quota fields. It is never returned or stored.
        return status, note, output if capture_output else {}

    @staticmethod
    def _antigravity_usage(output: str) -> dict:
        try:
            payload = json.loads(output)
            command = payload.get("command", {}) if isinstance(payload, dict) else {}
            data = command.get("data", {}) if isinstance(command, dict) else {}
            if not data and isinstance(payload, dict):
                data = payload.get("data", payload)
            groups = data.get("groups", []) if isinstance(data, dict) else []
            safe_groups = []
            for group in groups:
                if not isinstance(group, dict):
                    continue
                buckets = group.get("buckets", [])
                for bucket in buckets:
                    if not isinstance(bucket, dict):
                        continue
                    safe = {"name": group.get("name", "Quota"), "label": bucket.get("name", "Limit")}
                    if bucket.get("disabled"):
                        safe["remaining_percent"] = "disabled"
                    elif bucket.get("remaining_fraction") is not None:
                        safe["remaining_percent"] = round(float(bucket["remaining_fraction"]) * 100)
                    if bucket.get("reset_time"):
                        safe["reset_at"] = bucket["reset_time"]
                    safe_groups.append(safe)
                if not buckets:
                    safe = {key: group[key] for key in ("name", "label", "remaining_percent", "reset_at") if key in group}
                    if safe:
                        safe_groups.append(safe)
            return {"groups": safe_groups} if safe_groups else {}
        except (json.JSONDecodeError, AttributeError, TypeError):
            return {}

    def register(self, agent: dict) -> dict:
        if agent["adapter"] in {"exec", "copilot", "antigravity", "codex"}:
            executable = agent["command"][0] if agent["command"] else ""
            installed = bool(executable and shutil.which(executable))
            agent["available"] = installed and agent["adapter"] == "exec"
            note = "Configured command detected; credential verification required" if installed else "Configured executable was not found on PATH"
        else:
            agent["available"] = agent["adapter"] != "handoff"
            note = "Configured by operator"
        with db.transaction() as con:
            con.execute("""INSERT INTO agents(id,name,adapter,command,capabilities,file_globs,max_parallel,enabled,available,availability_note)
                VALUES(?,?,?,?,?,?,?,1,?,?)""",
                (agent["id"], agent["name"], agent["adapter"], db.dumps(agent["command"]),
                 db.dumps(agent["capabilities"]), db.dumps(agent["file_globs"]), agent["max_parallel"],
                 int(agent["available"]), note))
        return self.get(agent["id"])

    @staticmethod
    def _decode(agent: dict) -> dict:
        for key in ("command", "capabilities", "file_globs"):
            agent[key] = db.json.loads(agent[key])
        agent["credential_usage"] = db.json.loads(agent.get("credential_usage") or "{}")
        agent["enabled"] = bool(agent["enabled"])
        agent["available"] = bool(agent["available"])
        agent["connection_state"] = ("ready" if agent["available"] else
            "authenticated_policy_pending" if agent["adapter"] == "copilot" and agent["credential_status"] == "valid" else
            "sign_in_required" if agent["credential_status"] == "invalid" else "unverified")
        return agent
