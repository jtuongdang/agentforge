"""BYOK connection metadata and bounded model discovery.

Provider secrets are kept in the operating system credential store. SQLite stores
only a random reference, never the credential itself.
"""
from __future__ import annotations

import json
import secrets
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlsplit

from . import db

SERVICE_NAME = "devagent-orchestrator"
MAX_RESPONSE_BYTES = 1_000_000
TIMEOUT_SECONDS = 8
class ProviderError(RuntimeError):
    pass


class ProviderAdapter(Protocol):
    """Provider-specific catalog contract; credentials never enter persistent metadata."""
    def model_endpoint(self, endpoint: str) -> str: ...
    def request_headers(self, api_key: str | None) -> dict[str, str]: ...
    def parse_models(self, payload: dict) -> list[str]: ...


class _OpenAIAdapter:
    def model_endpoint(self, _endpoint: str) -> str:
        return "https://api.openai.com/v1/models"

    def request_headers(self, api_key: str | None) -> dict[str, str]:
        return {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}

    def parse_models(self, payload: dict) -> list[str]:
        return _named_models(payload.get("data", []), "id")


class _AnthropicAdapter:
    def model_endpoint(self, _endpoint: str) -> str:
        return "https://api.anthropic.com/v1/models?limit=100"

    def request_headers(self, api_key: str | None) -> dict[str, str]:
        return {"Accept": "application/json", "x-api-key": api_key or "",
                "anthropic-version": "2023-06-01"}

    def parse_models(self, payload: dict) -> list[str]:
        return _named_models(payload.get("data", []), "id")


class _GeminiAdapter:
    def model_endpoint(self, _endpoint: str) -> str:
        return "https://generativelanguage.googleapis.com/v1beta/models?pageSize=100"

    def request_headers(self, api_key: str | None) -> dict[str, str]:
        return {"Accept": "application/json", "x-goog-api-key": api_key or ""}

    def parse_models(self, payload: dict) -> list[str]:
        records = payload.get("models", [])
        names = [item.get("name") for item in records if isinstance(item, dict)
                 and "generateContent" in item.get("supportedGenerationMethods", [])] if isinstance(records, list) else []
        return sorted({name.removeprefix("models/") for name in names if isinstance(name, str) and name})[:100]


class _OllamaAdapter:
    def model_endpoint(self, endpoint: str) -> str:
        return _ollama_url(endpoint)

    def request_headers(self, _api_key: str | None) -> dict[str, str]:
        return {"Accept": "application/json"}

    def parse_models(self, payload: dict) -> list[str]:
        return _named_models(payload.get("models", []), "name")


def _named_models(records, field: str) -> list[str]:
    names = [item.get(field) for item in records if isinstance(item, dict)] if isinstance(records, list) else []
    return sorted({name for name in names if isinstance(name, str) and name})[:100]


PROVIDER_ADAPTERS: dict[str, ProviderAdapter] = {
    "openai": _OpenAIAdapter(), "anthropic": _AnthropicAdapter(),
    "gemini": _GeminiAdapter(), "ollama": _OllamaAdapter(),
}


def _keyring():
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
        backend = keyring.get_keyring()
        if isinstance(backend, FailKeyring) or getattr(backend, "priority", 0) <= 0:
            raise ProviderError("No operating-system credential store is available")
        # Static imports make the selected native backend visible to Nuitka; keyring
        # otherwise discovers platform plugins through entry points at runtime.
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring as NativeKeyring
            if not isinstance(backend, NativeKeyring):
                backend = NativeKeyring()
                keyring.set_keyring(backend)
        elif sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring
            if not isinstance(backend, WinVaultKeyring):
                backend = WinVaultKeyring()
                keyring.set_keyring(backend)
        elif sys.platform.startswith("linux"):
            # Linux has several approved desktop stores; import them so packaged
            # builds can discover their entry points without bundling plaintext stores.
            try:
                from keyring.backends import SecretService
            except ImportError:
                SecretService = None
            try:
                from keyring.backends import kwallet
            except ImportError:
                kwallet = None
            _ = (SecretService, kwallet)
        allowed = {"keyring.backends.macOS", "keyring.backends.Windows", "keyring.backends.SecretService",
                   "keyring.backends.kwallet"}
        backend = keyring.get_keyring()
        if type(backend).__module__ not in allowed:
            raise ProviderError("The active credential backend is not an approved OS keychain")
        return keyring
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError("Could not access the operating-system credential store") from exc


def credential_store_status() -> dict:
    try:
        backend = _keyring().get_keyring()
        return {"available": True, "store": "OS credential store",
                "backend": type(backend).__module__ + "." + type(backend).__name__}
    except ProviderError as exc:
        return {"available": False, "store": "OS credential store", "reason": str(exc)}


def _ollama_url(endpoint: str | None) -> str:
    base = (endpoint or "http://127.0.0.1:11434").rstrip("/")
    parsed = urlsplit(base)
    try:
        port = parsed.port
    except ValueError:
        raise ProviderError("Ollama endpoint port is invalid") from None
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"} or (port is not None and not 1 <= port <= 65535)):
        raise ProviderError("Ollama must use a local loopback HTTP endpoint")
    return base + "/api/tags"


def _metadata(row) -> dict:
    result = dict(row)
    result["models"] = json.loads(result.pop("models", "[]"))
    result["has_credential"] = bool(result.pop("secret_ref", None))
    result["agent_id"] = f"provider-{result['id']}"
    return result


def _sync_worker_agent(connection_id: str) -> None:
    current = _raw_connection(connection_id)
    if current is None:
        return
    current = dict(current)
    models = json.loads(current["models"] or "[]")
    enabled_model = current["state"] == "valid" and current["model"] in models
    credential_available = True
    if enabled_model and current["provider"] != "ollama":
        try:
            credential_available = bool(current["secret_ref"] and
                _keyring().get_password(SERVICE_NAME, current["secret_ref"]))
        except ProviderError:
            credential_available = False
    enabled_model = enabled_model and credential_available
    if not current["model"]:
        note = "Choose a model after checking the provider connection."
    elif not credential_available:
        note = "Provider credential is missing from the approved OS credential store."
    elif current["state"] != "valid":
        note = "Check the provider connection before scheduling this worker."
    elif current["model"] not in models:
        note = "The selected model is not in the latest supported model list."
    else:
        note = "Provider worker is available; it only runs when explicitly selected for a task."
    with db.transaction() as con:
        con.execute("""INSERT INTO agents(id,name,adapter,provider,command,capabilities,file_globs,max_parallel,
            enabled,available,availability_note) VALUES(?,?,?,?,?,?,?,?,1,?,?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name,adapter=excluded.adapter,provider=excluded.provider,
            command=excluded.command,capabilities=excluded.capabilities,file_globs=excluded.file_globs,
            available=excluded.available,availability_note=excluded.availability_note""",
            (f"provider-{connection_id}", f"{current['name']} ({current['provider']})", "provider",
             current["provider"], db.dumps([connection_id]),
             db.dumps(["analysis", "implementation", "documentation", "refactoring", "testing"]),
             db.dumps(["**/*"]), 1, int(enabled_model), note))


def list_connections() -> list[dict]:
    with db.connect() as con:
        rows = con.execute("SELECT * FROM provider_connections ORDER BY created_at,id").fetchall()
    return [_metadata(row) for row in rows]


def get_connection(connection_id: str) -> dict | None:
    current = _raw_connection(connection_id)
    return _metadata(current) if current else None


def _raw_connection(connection_id: str):
    with db.connect() as con:
        row = con.execute("SELECT * FROM provider_connections WHERE id=?", (connection_id,)).fetchone()
    return row


def create_connection(provider: str, name: str, model: str = "", api_key: str | None = None,
                      endpoint: str | None = None) -> dict:
    if provider not in PROVIDER_ADAPTERS:
        raise ProviderError("Unsupported provider")
    if provider != "ollama" and not (api_key and api_key.strip()):
        raise ProviderError("An API key is required for this provider")
    if provider == "ollama":
        if api_key:
            raise ProviderError("Ollama connection does not accept an API key")
        _ollama_url(endpoint)
        endpoint = (endpoint or "http://127.0.0.1:11434").rstrip("/")
    else:
        endpoint = ""
    connection_id = uuid.uuid4().hex
    secret_ref = secrets.token_urlsafe(24) if api_key else None
    keyring = _keyring() if api_key else None
    try:
        if keyring and secret_ref:
            keyring.set_password(SERVICE_NAME, secret_ref, api_key.strip())
        with db.transaction() as con:
            con.execute("""INSERT INTO provider_connections(id,provider,name,model,endpoint,secret_ref)
                VALUES(?,?,?,?,?,?)""", (connection_id, provider, name.strip(), model.strip(), endpoint or "", secret_ref))
    except Exception as exc:
        if keyring and secret_ref:
            try:
                keyring.delete_password(SERVICE_NAME, secret_ref)
            except Exception:
                pass
        if isinstance(exc, ProviderError):
            raise
        raise ProviderError("Could not save the provider connection") from exc
    _sync_worker_agent(connection_id)
    return get_connection(connection_id)


def update_connection(connection_id: str, *, name: str | None = None, model: str | None = None,
                      api_key: str | None = None) -> dict | None:
    current = _raw_connection(connection_id)
    if current is None:
        return None
    current = dict(current)
    secret_ref = None
    keyring = None
    if api_key is not None:
        if current["provider"] == "ollama" and api_key:
            raise ProviderError("Ollama connection does not accept an API key")
        if current["provider"] != "ollama" and not api_key.strip():
            raise ProviderError("API key cannot be empty")
        keyring = _keyring()
        secret_ref = secrets.token_urlsafe(24)
        keyring.set_password(SERVICE_NAME, secret_ref, api_key.strip())
    try:
        with db.transaction() as con:
            con.execute("""UPDATE provider_connections SET name=?,model=?,secret_ref=COALESCE(?,secret_ref),
                state=CASE WHEN ? IS NULL THEN state ELSE 'unverified' END,updated_at=CURRENT_TIMESTAMP
                WHERE id=?""", (name.strip() if name is not None else current["name"],
                                  model.strip() if model is not None else current["model"], secret_ref,
                                  secret_ref, connection_id))
        if secret_ref and current["secret_ref"]:
            try:
                _keyring().delete_password(SERVICE_NAME, current["secret_ref"])
            except Exception:
                # The new secret is active; stale OS-store material is safer than rolling back to old data.
                pass
    except Exception as exc:
        if keyring and secret_ref:
            try:
                keyring.delete_password(SERVICE_NAME, secret_ref)
            except Exception:
                pass
        raise ProviderError("Could not update the provider connection") from exc
    _sync_worker_agent(connection_id)
    return get_connection(connection_id)


def delete_connection(connection_id: str) -> bool:
    current = _raw_connection(connection_id)
    if current is None:
        return False
    current = dict(current)
    if current["secret_ref"]:
        try:
            _keyring().delete_password(SERVICE_NAME, current["secret_ref"])
        except Exception as exc:
            raise ProviderError("Could not remove the credential from the operating-system store") from exc
    with db.transaction() as con:
        con.execute("DELETE FROM provider_connections WHERE id=?", (connection_id,))
        con.execute("UPDATE agents SET enabled=0,available=0,availability_note=? WHERE id=?",
                    ("Provider connection was removed.", f"provider-{connection_id}"))
    return True


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request_models(provider: str, endpoint: str, api_key: str | None) -> list[str]:
    adapter = PROVIDER_ADAPTERS[provider]
    url = adapter.model_endpoint(endpoint)
    headers = adapter.request_headers(api_key)
    request = urllib.request.Request(url, headers=headers, method="GET")
    handlers = [urllib.request.ProxyHandler({})] if provider == "ollama" else []
    handlers.append(_NoRedirect())
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        # Avoid storing or returning provider response bodies, which may contain request details.
        raise ProviderError(f"Provider request failed with HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"Provider connection failed ({type(exc).__name__})") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProviderError("Provider response exceeded the 1 MB safety limit")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ProviderError("Provider returned an invalid model list") from None
    return adapter.parse_models(payload)


def check_connection(connection_id: str) -> dict:
    current = _raw_connection(connection_id)
    if current is None:
        raise ProviderError("Provider connection not found")
    current = dict(current)
    api_key = None
    if current["secret_ref"]:
        try:
            api_key = _keyring().get_password(SERVICE_NAME, current["secret_ref"])
        except Exception as exc:
            with db.transaction() as con:
                con.execute("UPDATE provider_connections SET state='unavailable',models='[]',checked_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (datetime.now(timezone.utc).isoformat(timespec="seconds"), connection_id))
            _sync_worker_agent(connection_id)
            raise ProviderError("Could not read the provider credential from the operating-system store") from exc
        if not api_key:
            with db.transaction() as con:
                con.execute("UPDATE provider_connections SET state='unavailable',models='[]',checked_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (datetime.now(timezone.utc).isoformat(timespec="seconds"), connection_id))
            _sync_worker_agent(connection_id)
            raise ProviderError("Provider credential is missing from the operating-system store")
    try:
        models = _request_models(current["provider"], current["endpoint"], api_key)
    except ProviderError as exc:
        with db.transaction() as con:
            con.execute("UPDATE provider_connections SET state='invalid',models='[]',checked_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (datetime.now(timezone.utc).isoformat(timespec="seconds"), connection_id))
        _sync_worker_agent(connection_id)
        raise exc
    with db.transaction() as con:
        con.execute("UPDATE provider_connections SET state='valid',models=?,checked_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (db.dumps(models), datetime.now(timezone.utc).isoformat(timespec="seconds"), connection_id))
    _sync_worker_agent(connection_id)
    return get_connection(connection_id)
