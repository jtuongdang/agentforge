"""Local verification primitives for device-bound, signed license entitlements.

The application never needs to persist a license key or transmit source code to
verify a hub-issued entitlement. Network refresh and hub administration are
kept outside this module so the verification rules can be tested in isolation.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

MAX_ENVELOPE_BYTES = 16 * 1024
DEVICE_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
VALID_STATUSES = {"trial", "active", "expired", "revoked", "banned"}


class EntitlementError(ValueError):
    """An entitlement is malformed, invalidly signed, or bound to another device."""


class LicenseUnavailable(RuntimeError):
    """The configured license hub could not provide a verifiable entitlement."""


class LicenseConsentRequired(RuntimeError):
    """The user must acknowledge the device identifier notice before network access."""


@dataclass(frozen=True)
class Entitlement:
    device_hash: str
    status: str
    issued_at: datetime
    not_before: datetime
    expires_at: datetime
    grace_until: datetime
    features: frozenset[str]
    license_id: str | None = None
    tier: str | None = None


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise EntitlementError(f"Invalid {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EntitlementError(f"Invalid {field}") from exc
    if parsed.tzinfo is None:
        raise EntitlementError(f"Invalid {field}: timezone required")
    return parsed.astimezone(timezone.utc)


def _decode_b64url(value: Any, field: str) -> bytes:
    if not isinstance(value, str) or len(value) > MAX_ENVELOPE_BYTES * 2:
        raise EntitlementError(f"Invalid {field}")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise EntitlementError(f"Invalid {field}") from exc


def verify_envelope(envelope: dict[str, Any], public_key_pem: bytes, expected_device_hash: str) -> Entitlement:
    """Verify exact signed payload bytes before parsing or trusting any claims."""
    if not DEVICE_HASH_RE.fullmatch(expected_device_hash):
        raise EntitlementError("Invalid local device hash")
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
        raise EntitlementError("Invalid entitlement envelope")
    payload = _decode_b64url(envelope["payload"], "payload")
    signature = _decode_b64url(envelope["signature"], "signature")
    if not payload or len(payload) > MAX_ENVELOPE_BYTES:
        raise EntitlementError("Entitlement payload exceeds the size limit")
    try:
        key = serialization.load_pem_public_key(public_key_pem)
    except Exception as exc:
        raise EntitlementError("Entitlement public key is invalid") from exc
    if not isinstance(key, rsa.RSAPublicKey):
        raise EntitlementError("Entitlement key must be RSA")
    try:
        key.verify(signature, payload, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=hashes.SHA256().digest_size), hashes.SHA256())
        claims = json.loads(payload)
    except InvalidSignature as exc:
        raise EntitlementError("Entitlement signature is invalid") from exc
    except EntitlementError:
        raise
    except Exception as exc:
        raise EntitlementError("Entitlement cannot be verified") from exc
    if not isinstance(claims, dict):
        raise EntitlementError("Invalid entitlement claims")
    if claims.get("version") != 1:
        raise EntitlementError("Unsupported entitlement version")
    device_hash = claims.get("device_hash")
    status = claims.get("status")
    if device_hash != expected_device_hash:
        raise EntitlementError("Entitlement belongs to a different device")
    if not isinstance(status, str) or status not in VALID_STATUSES:
        raise EntitlementError("Invalid entitlement status")
    features = claims.get("features", [])
    if not isinstance(features, list) or len(features) > 64 or any(not isinstance(item, str) for item in features):
        raise EntitlementError("Invalid entitlement features")
    issued_at = _utc(claims.get("issued_at"), "issued_at")
    not_before = _utc(claims.get("not_before"), "not_before")
    expires_at = _utc(claims.get("expires_at"), "expires_at")
    grace_until = _utc(claims.get("grace_until"), "grace_until")
    if not issued_at <= not_before < expires_at <= grace_until:
        raise EntitlementError("Entitlement validity window is inconsistent")
    return Entitlement(
        device_hash=device_hash,
        status=status,
        issued_at=issued_at,
        not_before=not_before,
        expires_at=expires_at,
        grace_until=grace_until,
        features=frozenset(features),
        license_id=claims.get("license_id") if isinstance(claims.get("license_id"), str) else None,
        tier=claims.get("tier") if isinstance(claims.get("tier"), str) else None,
    )


def hash_device_identifier(identifier: str) -> str:
    """Domain-separate and hash a locally obtained OS identifier before it leaves the device."""
    normalized = identifier.strip().lower()
    if not normalized or normalized in {"00000000-0000-0000-0000-000000000000", "unknown", "none"}:
        raise EntitlementError("A stable device identifier is unavailable")
    return hashlib.sha256(b"clinic-orchestrator:license-device:v1:" + normalized.encode("utf-8")).hexdigest()


def _read_os_identifier() -> str:
    system = platform.system()
    if system == "Darwin":
        result = subprocess.run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                capture_output=True, text=True, timeout=3, check=True)
        match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', result.stdout)
        if match:
            return match.group(1)
    elif system == "Windows":
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            return str(winreg.QueryValueEx(key, "MachineGuid")[0])
    elif system == "Linux":
        for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                value = Path(path).read_text(encoding="ascii").strip()
                if value:
                    return value
            except OSError:
                continue
    raise EntitlementError("A stable device identifier is unavailable")


def device_hash(identifier: str | None = None) -> str:
    """Return a one-way local device identifier; the raw OS identifier is never returned."""
    return hash_device_identifier(_read_os_identifier() if identifier is None else identifier)


def evaluate_entitlement(
    entitlement: Entitlement,
    *,
    now: datetime,
    last_server_time: datetime | None = None,
    online: bool = False,
    rollback_tolerance: timedelta = timedelta(minutes=5),
) -> dict[str, Any]:
    """Return UI-safe state; only `ai_execution` is gated, preserving local data access."""
    if now.tzinfo is None:
        raise ValueError("Current time must include a timezone")
    now = now.astimezone(timezone.utc)
    if last_server_time and now + rollback_tolerance < last_server_time.astimezone(timezone.utc):
        return {"status": "clock_rollback", "ai_execution": False, "reason": "System clock moved backwards.",
                "local_read": True, "local_export": True}
    if entitlement.status in {"revoked", "banned", "expired"}:
        return {"status": entitlement.status, "ai_execution": False, "reason": None,
                "local_read": True, "local_export": True}
    if now < entitlement.not_before:
        return {"status": "not_yet_valid", "ai_execution": False, "reason": None,
                "local_read": True, "local_export": True}
    if now <= entitlement.expires_at:
        return {"status": entitlement.status, "ai_execution": "ai_execution" in entitlement.features,
                "reason": None, "local_read": True, "local_export": True}
    if not online and now <= entitlement.grace_until:
        return {"status": "offline_grace", "ai_execution": "ai_execution" in entitlement.features,
                "reason": None, "local_read": True, "local_export": True}
    return {"status": "expired", "ai_execution": False, "reason": None,
            "local_read": True, "local_export": True}


def public_key_from_file(path: str | os.PathLike[str]) -> bytes:
    """Load only a public key. Private signing material belongs to the hub, never this client."""
    with Path(path).open("rb") as stream:
        return stream.read(16 * 1024 + 1)


class LicenseService:
    """Device-bound hub client and local cache. It never stores or logs a license key."""

    REFRESH_INTERVAL = timedelta(minutes=15)

    def __init__(self, clock=None):
        self._device_hash: str | None = None
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def required() -> bool:
        configured = os.getenv("CLINIC_ORCHESTRATOR_LICENSE_REQUIRED")
        if configured is not None:
            return configured.strip().lower() in {"1", "true", "yes"}
        return bool(getattr(sys, "frozen", False) or globals().get("__compiled__"))

    @staticmethod
    def _configuration() -> tuple[str, bytes] | None:
        base_url = os.getenv("CLINIC_ORCHESTRATOR_LICENSE_HUB_URL", "").strip().rstrip("/")
        key_file = os.getenv("CLINIC_ORCHESTRATOR_LICENSE_PUBLIC_KEY_FILE", "").strip()
        if not base_url and not key_file:
            return None
        if not base_url or not key_file:
            raise LicenseUnavailable("License hub configuration is incomplete")
        parsed = urlparse(base_url)
        if (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"})):
            raise LicenseUnavailable("License hub must use HTTPS")
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise LicenseUnavailable("License hub URL is invalid")
        try:
            public_key = public_key_from_file(key_file)
        except OSError as exc:
            raise LicenseUnavailable("License public key is unavailable") from exc
        if len(public_key) > 16 * 1024:
            raise LicenseUnavailable("License public key is too large")
        return base_url, public_key

    def _device(self) -> str:
        if self._device_hash is None:
            self._device_hash = device_hash()
        return self._device_hash

    @staticmethod
    def _cache_row():
        from . import db
        with db.connect() as con:
            return con.execute("SELECT * FROM license_cache WHERE id=1").fetchone()

    @staticmethod
    def _has_consent() -> bool:
        from . import db
        with db.connect() as con:
            return con.execute("SELECT 1 FROM system_settings WHERE key='license.privacy_ack_at'").fetchone() is not None

    @staticmethod
    def grant_consent() -> None:
        from . import db
        with db.transaction() as con:
            con.execute("INSERT INTO system_settings(key,value) VALUES('license.privacy_ack_at',?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP",
                        (datetime.now(timezone.utc).isoformat(),))

    def _persist(self, envelope: dict[str, str], entitlement: Entitlement, device: str) -> None:
        from . import db
        now = datetime.now(timezone.utc).isoformat()
        serialized = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
        with db.transaction() as con:
            con.execute("""INSERT INTO license_cache(id,device_hash,signed_envelope,status,last_server_time,last_wall_time)
                VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET device_hash=excluded.device_hash,
                signed_envelope=excluded.signed_envelope,status=excluded.status,
                last_server_time=excluded.last_server_time,last_wall_time=excluded.last_wall_time,
                updated_at=CURRENT_TIMESTAMP""",
                (device, serialized, entitlement.status, entitlement.issued_at.isoformat(), now))

    def _hub_post(self, operation: str, body: dict[str, str]) -> tuple[Any, bytes, str]:
        configuration = self._configuration()
        if configuration is None:
            raise LicenseUnavailable("License hub is not configured")
        base_url, public_key = configuration
        device = self._device()
        try:
            import httpx
            response = httpx.post(f"{base_url}/api/v1/{operation}", json=body,
                                  timeout=httpx.Timeout(8.0, connect=3.0),
                                  follow_redirects=False, trust_env=False,
                                  headers={"Accept": "application/json"})
        except Exception as exc:
            raise LicenseUnavailable("License hub could not be reached") from exc
        if len(response.content) > MAX_ENVELOPE_BYTES * 2:
            raise LicenseUnavailable("License hub response exceeds the size limit")
        if response.status_code != 200:
            raise LicenseUnavailable(f"License hub rejected the request (HTTP {response.status_code})")
        try:
            result = response.json()
        except Exception as exc:
            raise LicenseUnavailable("License hub returned an invalid response") from exc
        return result, public_key, device

    def _request_entitlement(self, operation: str, body: dict[str, str]) -> tuple[dict[str, str], Entitlement, str]:
        envelope, public_key, device = self._hub_post(operation, body)
        try:
            entitlement = verify_envelope(envelope, public_key, device)
        except Exception as exc:
            raise LicenseUnavailable("License hub returned an invalid entitlement") from exc
        return envelope, entitlement, device

    def refresh(self) -> dict[str, Any]:
        if self._configuration() is None:
            return self.status()
        if not self._has_consent():
            return self.status()
        envelope, entitlement, device = self._request_entitlement("devices/verify", {"device_hash": self._device()})
        self._persist(envelope, entitlement, device)
        return self.status()

    def activate(self, license_key: str) -> dict[str, Any]:
        if self._configuration() is not None and not self._has_consent():
            raise LicenseConsentRequired("Review and accept the device privacy notice before activation.")
        normalized = license_key.strip()
        if not normalized or len(normalized) > 256:
            raise ValueError("License key must contain 1 to 256 characters")
        envelope, entitlement, device = self._request_entitlement(
            "licenses/activate", {"device_hash": self._device(), "license_key": normalized})
        self._persist(envelope, entitlement, device)
        return self.status()

    def deactivate(self, license_key: str) -> dict[str, Any]:
        if self._configuration() is not None and not self._has_consent():
            raise LicenseConsentRequired("Review and accept the device privacy notice before deactivation.")
        normalized = license_key.strip()
        if not normalized or len(normalized) > 256:
            raise ValueError("License key must contain 1 to 256 characters")
        result, _, device = self._hub_post("licenses/deactivate",
            {"device_hash": self._device(), "license_key": normalized})
        if not isinstance(result, dict) or result.get("deactivated") is not True:
            raise LicenseUnavailable("License hub did not confirm device deactivation")
        from . import db
        with db.transaction() as con:
            con.execute("DELETE FROM license_cache WHERE id=1 AND device_hash=?", (device,))
        return self.status()

    def status(self) -> dict[str, Any]:
        required = self.required()
        if not required and self._configuration() is None:
            return {"required": False, "configured": False, "status": "development", "ai_execution": True,
                    "local_read": True, "local_export": True, "days_remaining": None}
        try:
            configuration = self._configuration()
            device = self._device()
        except (LicenseUnavailable, EntitlementError, OSError, subprocess.SubprocessError):
            return {"required": required, "configured": False, "status": "configuration_error",
                    "ai_execution": False, "local_read": True, "local_export": True, "days_remaining": None}
        if configuration is None:
            return {"required": required, "configured": False, "status": "unconfigured",
                    "ai_execution": not required, "local_read": True, "local_export": True,
                    "days_remaining": None}
        if not self._has_consent():
            return {"required": required, "configured": True, "status": "consent_required",
                    "ai_execution": False, "local_read": True, "local_export": True,
                    "days_remaining": None}
        row = self._cache_row()
        if row is None:
            return {"required": required, "configured": True, "status": "trial_pending",
                    "ai_execution": False, "local_read": True, "local_export": True,
                    "days_remaining": None}
        if row["device_hash"] != device:
            return {"required": required, "configured": True, "status": "device_changed",
                    "ai_execution": False, "local_read": True, "local_export": True,
                    "days_remaining": None}
        try:
            entitlement = verify_envelope(json.loads(row["signed_envelope"]), configuration[1], device)
        except Exception:
            return {"required": required, "configured": True, "status": "invalid_entitlement",
                    "ai_execution": False, "local_read": True, "local_export": True,
                    "days_remaining": None}
        now = self._clock().astimezone(timezone.utc)
        last_check = datetime.fromisoformat(row["updated_at"]).replace(tzinfo=timezone.utc)
        last_server_time = datetime.fromisoformat(row["last_server_time"]).replace(tzinfo=timezone.utc)
        last_wall_time = datetime.fromisoformat(row["last_wall_time"]).replace(tzinfo=timezone.utc)
        rollback_reference = max(last_server_time, last_wall_time)
        if now - last_wall_time >= timedelta(minutes=1):
            from . import db
            with db.transaction() as con:
                con.execute("UPDATE license_cache SET last_wall_time=? WHERE id=1 AND device_hash=?",
                            (now.isoformat(), device))
        online = now - last_check < self.REFRESH_INTERVAL
        state = evaluate_entitlement(entitlement, now=now, last_server_time=rollback_reference, online=online)
        return {"required": required, "configured": True, "status": state["status"],
                "ai_execution": state["ai_execution"], "local_read": True, "local_export": True,
                "days_remaining": max(0, (entitlement.expires_at.date() - now.date()).days),
                "expires_at": entitlement.expires_at.isoformat(), "tier": entitlement.tier}

    def dispatch_allowed(self) -> bool:
        return bool(self.status()["ai_execution"])

    async def refresh_loop(self, interval_seconds: int = 300) -> None:
        import asyncio
        while True:
            try:
                status = self.status()
                if status["required"] or status["configured"]:
                    await asyncio.to_thread(self.refresh)
            except Exception:
                # A failed refresh leaves the last verified, device-bound cache intact.
                pass
            await asyncio.sleep(interval_seconds)


license_service = LicenseService()
