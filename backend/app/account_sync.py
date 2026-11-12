"""Allowlisted account metadata, without executable paths or provider credentials."""
from __future__ import annotations

import hashlib
import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone

PROVIDERS = {"codex", "copilot", "antigravity", "github", "openai", "anthropic", "gemini", "ollama", "custom", "handoff"}
STATUSES = {"unchecked", "valid", "invalid", "error", "unsupported", "not_applicable", "unknown", "ready", "needs_key"}


def github_account() -> tuple[str, str | None]:
    """Explicit sync may query the official CLI for a username, never an auth token."""
    executable = shutil.which("gh")
    if not executable:
        return "unsupported", None
    try:
        result = subprocess.run([executable, "api", "user", "--jq", ".login"],
                                capture_output=True, timeout=12, check=False)
        value = result.stdout.decode("utf-8", errors="replace").strip()
        if result.returncode == 0 and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", value):
            return "valid", value
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown", None


def connection_snapshot(agents: list[dict], github: tuple[str, str | None] | None = None) -> dict:
    connections = []
    for agent in agents:
        provider = agent.get("provider") if agent.get("adapter") == "provider" else agent.get("adapter")
        provider = "custom" if provider == "exec" else provider
        if provider not in PROVIDERS:
            continue
        identifier = str(agent.get("id", ""))
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", identifier):
            identifier = "agent-" + hashlib.sha256(identifier.encode()).hexdigest()[:40]
        status = agent.get("credential_status", "unchecked")
        if status not in STATUSES:
            status = "unknown"
        checked = agent.get("credential_checked_at")
        try:
            if checked:
                instant = datetime.fromisoformat(checked.replace("Z", "+00:00"))
                checked = instant.astimezone(timezone.utc).isoformat() if instant.tzinfo else None
        except (ValueError, TypeError, AttributeError):
            checked = None
        connections.append({"connection_id": identifier, "provider": provider, "status": status,
                            "account_label": None, "checked_at": checked})
    if github is not None:
        status, label = github
        connections.append({"connection_id": "github-repository-account", "provider": "github", "status": status,
                            "account_label": label, "checked_at": datetime.now(timezone.utc).isoformat()})
    if len(connections) > 20:
        raise ValueError("Hub accepts up to 20 account connections per device report")
    system = {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}.get(platform.system(), "unknown")
    return {"platform": system, "app_version": "0.1.0", "connections": connections}
