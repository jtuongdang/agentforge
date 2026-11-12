from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.license import (EntitlementError, LicenseService, device_hash, evaluate_entitlement,
                         hash_device_identifier, verify_envelope)


NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def signing_keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(serialization.Encoding.PEM,
                                                  serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, public_pem


def signed_claims(signing_keys, claims):
    private, _ = signing_keys
    raw = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    signature = private.sign(raw, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                           salt_length=hashes.SHA256().digest_size), hashes.SHA256())
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
    return {"payload": encode(raw), "signature": encode(signature)}


def claims_for(device):
    return {"version": 1, "device_hash": device, "status": "trial",
            "issued_at": "2026-11-12T11:00:00Z", "not_before": "2026-11-12T11:00:00Z",
            "expires_at": "2026-11-26T11:00:00Z", "grace_until": "2026-12-03T11:00:00Z",
            "features": ["ai_execution"], "license_id": "lic_test", "tier": "trial"}


def test_device_identifier_is_domain_separated_hash_only():
    digest = hash_device_identifier("  Machine-ID-123  ")
    assert digest == hash_device_identifier("machine-id-123")
    assert len(digest) == 64
    assert "machine-id-123" not in digest
    with pytest.raises(EntitlementError):
        device_hash("00000000-0000-0000-0000-000000000000")


def test_entitlement_verification_accepts_valid_claims(signing_keys):
    device = "a" * 64
    entitlement = verify_envelope(signed_claims(signing_keys, claims_for(device)), signing_keys[1], device)
    assert entitlement.device_hash == device
    assert entitlement.status == "trial"
    assert entitlement.features == {"ai_execution"}


def test_entitlement_rejects_tampering_wrong_device_and_invalid_key(signing_keys):
    device = "a" * 64
    envelope = signed_claims(signing_keys, claims_for(device))
    other = "b" * 64
    with pytest.raises(EntitlementError, match="different device"):
        verify_envelope(envelope, signing_keys[1], other)
    envelope["signature"] = base64.urlsafe_b64encode(b"bad signature").decode().rstrip("=")
    with pytest.raises(EntitlementError, match="signature"):
        verify_envelope(envelope, signing_keys[1], device)
    with pytest.raises(EntitlementError, match="invalid"):
        verify_envelope(signed_claims(signing_keys, claims_for(device)), b"not a public key", device)


@pytest.mark.parametrize("status", ["expired", "revoked", "banned"])
def test_blocked_status_keeps_local_read_and_export(signing_keys, status):
    device = "a" * 64
    claims = claims_for(device) | {"status": status}
    entitlement = verify_envelope(signed_claims(signing_keys, claims), signing_keys[1], device)
    result = evaluate_entitlement(entitlement, now=NOW, online=True)
    assert result["ai_execution"] is False
    assert result["local_read"] is True
    assert result["local_export"] is True


def test_expiry_grace_and_clock_rollback_are_explicit(signing_keys):
    device = "a" * 64
    entitlement = verify_envelope(signed_claims(signing_keys, claims_for(device)), signing_keys[1], device)
    after_expiry = datetime(2026, 11, 28, tzinfo=timezone.utc)
    offline = evaluate_entitlement(entitlement, now=after_expiry, online=False)
    assert offline["status"] == "offline_grace" and offline["ai_execution"] is True
    online = evaluate_entitlement(entitlement, now=after_expiry, online=True)
    assert online["status"] == "expired" and online["ai_execution"] is False
    rollback = evaluate_entitlement(entitlement, now=NOW,
                                    last_server_time=NOW + timedelta(hours=2), online=False)
    assert rollback["status"] == "clock_rollback" and rollback["ai_execution"] is False
    assert rollback["local_read"] and rollback["local_export"]


def test_hub_refresh_and_activation_cache_only_signed_device_entitlement(tmp_path, monkeypatch, signing_keys):
    from types import SimpleNamespace
    from app import db, license as license_module

    device = "c" * 64
    key = "CO-TEST-LICENSE-DO-NOT-PERSIST"
    public_pem = signing_keys[1]
    public_file = tmp_path / "license-public.pem"
    public_file.write_bytes(public_pem)
    monkeypatch.setenv("CLINIC_ORCHESTRATOR_LICENSE_REQUIRED", "true")
    monkeypatch.setenv("CLINIC_ORCHESTRATOR_LICENSE_HUB_URL", "https://license.example.invalid")
    monkeypatch.setenv("CLINIC_ORCHESTRATOR_LICENSE_PUBLIC_KEY_FILE", str(public_file))
    monkeypatch.setattr(license_module, "device_hash", lambda: device)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "license.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    db.init_db()
    sent = []

    def post(url, *, json, **_kwargs):
        sent.append((url, json))
        if url.endswith("/licenses/deactivate"):
            result = {"deactivated": True}
            return SimpleNamespace(status_code=200, content=b'{"deactivated":true}', json=lambda: result)
        response_claims = claims_for(device) | {"status": "active", "tier": "pro"}
        envelope = signed_claims(signing_keys, response_claims)
        return SimpleNamespace(status_code=200, content=b"signed", json=lambda: envelope)

    monkeypatch.setattr("httpx.post", post)
    service = LicenseService(clock=lambda: NOW + timedelta(hours=1))
    service.grant_consent()
    result = service.activate(key)
    assert result["status"] == "active"
    assert result["ai_execution"] is True
    assert sent[0][0] == "https://license.example.invalid/api/v1/licenses/activate"
    assert sent[0][1] == {"device_hash": device, "license_key": key}
    with db.connect() as con:
        row = con.execute("SELECT device_hash,signed_envelope,status FROM license_cache WHERE id=1").fetchone()
    assert row["device_hash"] == device and row["status"] == "active"
    assert key not in row["signed_envelope"]
    assert key.encode() not in db.DB_PATH.read_bytes()
    with db.transaction() as con:
        con.execute("UPDATE license_cache SET last_wall_time=? WHERE id=1",
                    ((NOW + timedelta(days=2)).isoformat(),))
    rollback = service.status()
    assert rollback["status"] == "clock_rollback"
    assert rollback["ai_execution"] is False
    assert rollback["local_read"] and rollback["local_export"]
    result = service.deactivate(key)
    assert result["status"] == "trial_pending"
    assert sent[-1][0].endswith("/api/v1/licenses/deactivate")
    assert sent[-1][1] == {"device_hash": device, "license_key": key}
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM license_cache").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM system_settings WHERE key='license.privacy_ack_at'").fetchone()[0] == 1


def test_license_service_requires_https_except_loopback(tmp_path, monkeypatch, signing_keys):
    from app.license import LicenseUnavailable

    public_file = tmp_path / "public.pem"
    public_file.write_bytes(signing_keys[1])
    monkeypatch.setenv("CLINIC_ORCHESTRATOR_LICENSE_PUBLIC_KEY_FILE", str(public_file))
    monkeypatch.setenv("CLINIC_ORCHESTRATOR_LICENSE_HUB_URL", "http://license.example.invalid")
    with pytest.raises(LicenseUnavailable, match="HTTPS"):
        LicenseService().refresh()
    monkeypatch.setenv("CLINIC_ORCHESTRATOR_LICENSE_HUB_URL", "http://127.0.0.1:8080")
    assert LicenseService()._configuration()[0] == "http://127.0.0.1:8080"


def test_license_device_hash_is_not_sent_before_privacy_consent(tmp_path, monkeypatch, signing_keys):
    from app import db, license as license_module

    public_file = tmp_path / "public.pem"
    public_file.write_bytes(signing_keys[1])
    monkeypatch.setenv("CLINIC_ORCHESTRATOR_LICENSE_REQUIRED", "true")
    monkeypatch.setenv("CLINIC_ORCHESTRATOR_LICENSE_HUB_URL", "https://license.example.invalid")
    monkeypatch.setenv("CLINIC_ORCHESTRATOR_LICENSE_PUBLIC_KEY_FILE", str(public_file))
    monkeypatch.setattr(license_module, "device_hash", lambda: "d" * 64)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "consent.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    db.init_db()
    monkeypatch.setattr("httpx.post", lambda *_args, **_kwargs: pytest.fail("hub request before consent"))
    service = LicenseService()

    assert service.status()["status"] == "consent_required"
    assert service.refresh()["status"] == "consent_required"
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM license_cache").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM system_settings WHERE key='license.privacy_ack_at'").fetchone()[0] == 0
    service.grant_consent()
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM system_settings WHERE key='license.privacy_ack_at'").fetchone()[0] == 1
