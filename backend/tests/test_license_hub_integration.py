from __future__ import annotations

import base64
import os
import shutil
import socket
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.license import verify_envelope


ROOT = Path(__file__).resolve().parents[2]
HUB_DLL = ROOT / "license-hub" / "bin" / "Debug" / "net8.0" / "LicenseHub.dll"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http(url: str, process: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"License Hub exited during startup; see {log_path}")
        try:
            response = httpx.get(url, timeout=0.5, trust_env=False)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    pytest.fail(f"License Hub did not become ready; see {log_path}")


@pytest.fixture
def live_hub(tmp_path):
    mongod = shutil.which("mongod")
    dotnet = shutil.which("dotnet")
    external_mongo_uri = os.getenv("CLINIC_ORCHESTRATOR_TEST_MONGODB_URI")
    if not dotnet or (not mongod and not external_mongo_uri):
        pytest.skip("Live License Hub integration requires local mongod and .NET 8")
    if not HUB_DLL.is_file():
        pytest.skip("Build license-hub/LicenseHub.csproj before running the optional live integration test")

    mongo_port = _free_port()
    hub_port = _free_port()
    mongo_log = tmp_path / "mongod.log"
    hub_log = tmp_path / "license-hub.log"
    hub_stream = hub_log.open("wb")
    mongo_process = None
    mongo_stream = None
    hub_process = None
    try:
        if not external_mongo_uri:
            mongo_path = tmp_path / "mongo"
            mongo_path.mkdir()
            mongo_stream = mongo_log.open("wb")
            mongo_process = subprocess.Popen([
                mongod, "--dbpath", str(mongo_path), "--bind_ip", "127.0.0.1", "--port", str(mongo_port),
                "--quiet", "--setParameter", "diagnosticDataCollectionEnabled=false"],
                stdout=mongo_stream, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if mongo_process.poll() is not None:
                    pytest.fail(f"Temporary mongod exited; see {mongo_log}")
                try:
                    with socket.create_connection(("127.0.0.1", mongo_port), timeout=0.25):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                pytest.fail(f"Temporary mongod did not become ready; see {mongo_log}")

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_file = tmp_path / "signing-private.pem"
        private_file.write_bytes(private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        private_file.chmod(0o600)
        public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        environment = os.environ.copy()
        environment.update({
            "MONGODB_URI": external_mongo_uri or f"mongodb://127.0.0.1:{mongo_port}/?directConnection=true",
            # Atlas deployments may limit database names to 38 bytes.
            "LICENSE_DATABASE": f"LicenseHubIT_{uuid.uuid4().hex[:24]}",
            "LICENSE_SIGNING_PRIVATE_KEY_PATH": str(private_file),
            "LICENSE_KEY_PEPPER": base64.b64encode(bytes(range(32))).decode("ascii"),
            "LICENSE_ADMIN_TOKEN": "local-integration-admin-token-not-for-production-0001",
            "ASPNETCORE_URLS": f"http://127.0.0.1:{hub_port}",
            "ASPNETCORE_ENVIRONMENT": "Production",
            "GOOGLE_AUTH_ENABLED": "false",
            "DOTNET_NOLOGO": "1",
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        })
        hub_process = subprocess.Popen([dotnet, str(HUB_DLL)], cwd=ROOT, env=environment,
                                       stdout=hub_stream, stderr=subprocess.STDOUT)
        base_url = f"http://127.0.0.1:{hub_port}"
        _wait_http(f"{base_url}/health", hub_process, hub_log)
        yield base_url, public_pem
    finally:
        for process in (hub_process, mongo_process):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        if mongo_stream is not None:
            mongo_stream.close()
        hub_stream.close()


def test_live_hub_trial_activation_device_limit_transfer_revoke_and_privacy(live_hub):
    base_url, public_pem = live_hub
    admin = {"Authorization": "Bearer local-integration-admin-token-not-for-production-0001"}
    first_device, second_device = "a" * 64, "b" * 64

    invalid_payload = httpx.post(f"{base_url}/api/v1/devices/verify",
        json={"device_hash": first_device, "source_code": "must be rejected"}, trust_env=False)
    assert invalid_payload.status_code == 400

    first_response = httpx.post(f"{base_url}/api/v1/devices/verify",
        json={"device_hash": first_device}, trust_env=False)
    assert first_response.status_code == 200
    first_entitlement = verify_envelope(first_response.json(), public_pem, first_device)
    assert first_entitlement.status == "trial"
    assert abs((first_entitlement.expires_at - first_entitlement.issued_at).total_seconds() - 14 * 86400) < 2

    repeated = httpx.post(f"{base_url}/api/v1/devices/verify",
        json={"device_hash": first_device}, trust_env=False)
    assert verify_envelope(repeated.json(), public_pem, first_device).expires_at == first_entitlement.expires_at
    assert verify_envelope(httpx.post(f"{base_url}/api/v1/devices/verify",
        json={"device_hash": second_device}, trust_env=False).json(), public_pem, second_device).status == "trial"

    created = httpx.post(f"{base_url}/api/v1/admin/licenses", headers=admin,
        json={"tier": "lifetime", "max_devices": 1, "expires_at": None}, trust_env=False)
    assert created.status_code == 201
    license_data = created.json()
    key = license_data["license_key"]
    assert key.startswith("CO-")

    def activate(device: str):
        return httpx.post(f"{base_url}/api/v1/licenses/activate",
            json={"device_hash": device, "license_key": key}, trust_env=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(activate, (first_device, second_device)))
    assert sorted(response.status_code for response in results) == [200, 409]
    winner_index = next(index for index, response in enumerate(results) if response.status_code == 200)
    winner, transfer_target = (first_device, second_device) if winner_index == 0 else (second_device, first_device)
    active = verify_envelope(results[winner_index].json(), public_pem, winner)
    assert active.status == "active" and active.tier == "lifetime"

    idempotent = activate(winner)
    assert idempotent.status_code == 200
    released = httpx.post(f"{base_url}/api/v1/licenses/deactivate",
        json={"device_hash": winner, "license_key": key}, trust_env=False)
    assert released.status_code == 200 and released.json()["deactivated"] is True
    transferred = activate(transfer_target)
    assert transferred.status_code == 200
    assert verify_envelope(transferred.json(), public_pem, transfer_target).status == "active"

    license_id = license_data["id"]
    revoked = httpx.post(f"{base_url}/api/v1/admin/licenses/{license_id}/revoke", headers=admin,
                         trust_env=False)
    assert revoked.status_code == 200
    after_revoke = httpx.post(f"{base_url}/api/v1/devices/verify",
        json={"device_hash": transfer_target}, trust_env=False)
    assert verify_envelope(after_revoke.json(), public_pem, transfer_target).status == "revoked"
    assert datetime.now(timezone.utc) >= first_entitlement.issued_at


def test_admin_console_user_management_auth_and_account_license_enforcement(live_hub):
    base_url, public_pem = live_hub
    admin = {"Authorization": "Bearer local-integration-admin-token-not-for-production-0001"}
    page = httpx.get(base_url + "/admin/", trust_env=False)
    assert page.status_code == 200 and "Account Admin" in page.text
    assert httpx.get(base_url + "/ready", trust_env=False).status_code == 200
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer"
    for asset in ("admin.css", "admin.js"):
        assert httpx.get(base_url + "/admin/" + asset, trust_env=False).status_code == 200
    for name in ("summary", "users", "licenses", "devices"):
        assert httpx.get(base_url + "/api/v1/admin/" + name, trust_env=False).status_code == 401
    user_body = {"display_name": "Synthetic Admin Fixture", "email": "fixture@example.invalid", "is_enabled": True}
    assert httpx.post(base_url + "/api/v1/admin/users", json=user_body, trust_env=False).status_code == 401
    invalid = httpx.post(base_url + "/api/v1/admin/users", headers=admin,
                         json={**user_body, "email": "invalid"}, trust_env=False)
    assert invalid.status_code == 400
    created = httpx.post(base_url + "/api/v1/admin/users", headers=admin, json=user_body, trust_env=False)
    assert created.status_code == 201
    user_id = created.json()["id"]
    duplicate = httpx.post(base_url + "/api/v1/admin/users", headers=admin,
                           json={**user_body, "email": "FIXTURE@EXAMPLE.INVALID"}, trust_env=False)
    assert duplicate.status_code == 409
    assert httpx.get(base_url + "/api/v1/admin/users?limit=101", headers=admin, trust_env=False).status_code == 400
    user_page = httpx.get(base_url + "/api/v1/admin/users?limit=1", headers=admin, trust_env=False).json()
    assert len(user_page["items"]) == 1 and user_page["items"][0]["id"] == user_id

    license_response = httpx.post(base_url + "/api/v1/admin/licenses", headers=admin,
        json={"tier": "lifetime", "max_devices": 1, "expires_at": None, "user_id": user_id}, trust_env=False)
    assert license_response.status_code == 201
    key = license_response.json()["license_key"]
    device = "c" * 64
    assert httpx.post(base_url + "/api/v1/devices/verify", json={"device_hash": device}, trust_env=False).status_code == 200
    activation = {"device_hash": device, "license_key": key}
    assert httpx.post(base_url + "/api/v1/licenses/activate", json=activation, trust_env=False).status_code == 200
    disabled = httpx.patch(base_url + "/api/v1/admin/users/" + user_id, headers=admin,
                           json={**user_body, "is_enabled": False}, trust_env=False)
    assert disabled.status_code == 200
    blocked = httpx.post(base_url + "/api/v1/devices/verify", json={"device_hash": device}, trust_env=False)
    assert verify_envelope(blocked.json(), public_pem, device).status == "banned"
    assert httpx.post(base_url + "/api/v1/licenses/activate", json=activation, trust_env=False).status_code == 403
    assert httpx.patch(base_url + "/api/v1/admin/users/" + user_id, headers=admin,
                      json=user_body, trust_env=False).status_code == 200
    restored = httpx.post(base_url + "/api/v1/devices/verify", json={"device_hash": device}, trust_env=False)
    assert verify_envelope(restored.json(), public_pem, device).status == "active"
    for name in ("summary", "users", "licenses", "devices"):
        response = httpx.get(base_url + "/api/v1/admin/" + name, headers=admin, trust_env=False)
        assert response.status_code == 200
        assert key not in response.text
        assert "license_key_hash" not in response.text and "token_hash" not in response.text
    summary = httpx.get(base_url + "/api/v1/admin/summary", headers=admin, trust_env=False).json()
    assert summary == {"users": 1, "enabled_users": 1, "licenses": 1, "devices": 1,
                       "google_identities": 0, "account_devices": 0, "active_sessions": 0}
