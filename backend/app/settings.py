"""Centralized local application settings and writable data locations."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "DevAgent Orchestrator"
ROOT = Path(__file__).resolve().parents[2]


def default_data_dir() -> Path:
    configured = os.getenv("CLINIC_ORCHESTRATOR_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if not getattr(sys, "frozen", False):
        return ROOT / "data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        return Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "devagent-orchestrator"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    api_host: str
    api_port: int
    api_token_file: Path
    log_retention_days: int = 7

    @classmethod
    def load(cls) -> "Settings":
        data_dir = default_data_dir()
        token_file = Path(os.getenv("CLINIC_ORCHESTRATOR_API_TOKEN_FILE", data_dir / "api-token")).expanduser()
        try:
            port = int(os.getenv("CLINIC_ORCHESTRATOR_PORT", "8765"))
        except ValueError as exc:
            raise ValueError("CLINIC_ORCHESTRATOR_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("CLINIC_ORCHESTRATOR_PORT must be between 1 and 65535")
        host = os.getenv("CLINIC_ORCHESTRATOR_HOST", "127.0.0.1")
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("The local desktop API host must resolve to loopback")
        return cls(data_dir=data_dir, api_host=host, api_port=port, api_token_file=token_file)


settings = Settings.load()
