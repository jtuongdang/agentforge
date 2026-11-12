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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

MAX_ENVELOPE_BYTES = 16 * 1024
DEVICE_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
VALID_STATUSES = {"trial", "active", "expired", "revoked", "banned"}


class EntitlementError(ValueError):
    """An entitlement is malformed, invalidly signed, or bound to another device."""


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
