import asyncio
from types import SimpleNamespace

import pytest

from app import db
from app.agent_login import AgentLogin
from app.registry import AgentRegistry


@pytest.fixture(autouse=True)
def synthetic_environment(monkeypatch):
    for name in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("app.agent_login.shutil.which", lambda name: f"/fixture/{name}")
    monkeypatch.setattr("app.agent_login.providers.credential_store_status", lambda: {"available": True})


def test_allowlist_and_fail_closed_storage(monkeypatch):
    async def run():
        manager = AgentLogin()
        for provider in ("unknown", "agy", "google", "github; echo secret"):
            with pytest.raises(ValueError, match="no supported"):
                await manager.start(provider)
        with pytest.raises(ValueError, match="Acknowledge"):
            await manager.start("github")
        monkeypatch.setattr("app.agent_login.providers.credential_store_status", lambda: {"available": False})
        with pytest.raises(ValueError, match="credential store"):
            await manager.start("codex")
    asyncio.run(run())


def test_environment_override_exposes_only_variable_name(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "synthetic-secret-must-not-leak")
    async def run():
        with pytest.raises(ValueError) as error:
            await AgentLogin().start("github", True)
        assert "GH_TOKEN" in str(error.value)
        assert "synthetic-secret" not in str(error.value)
    asyncio.run(run())


def test_login_drops_raw_output_and_clears_one_time_code(monkeypatch):
    captured = {}
    async def run():
        release = asyncio.Event()
        class Output:
            count = 0
            async def read(self, size):
                self.count += 1
                if self.count == 1:
                    return b"First copy your one-time code: ABCD-1234\nsecret-token https://evil.test/?token=secret\n"
                await release.wait()
                return b""
        class Input:
            def write(self, data): captured["stdin"] = data
            async def drain(self): pass
            def close(self): pass
        process = SimpleNamespace(stdout=Output(), stdin=Input())
        async def wait(): return 0
        process.wait = wait
        async def create(*args, **kwargs):
            captured.update(args=args, kwargs=kwargs)
            return process
        async def terminate(*args, **kwargs): captured["terminated"] = True
        monkeypatch.setattr("app.agent_login.create_managed_process", create)
        monkeypatch.setattr("app.agent_login.terminate_process_tree", terminate)
        monkeypatch.setattr("app.agent_login.webbrowser.open", lambda url: captured.update(browser=url))
        manager = AgentLogin()
        assert (await manager.start("github", True))["state"] == "running"
        for _ in range(100):
            if captured.get("browser"): break
            await asyncio.sleep(.001)
        assert manager.status()["device_code"] == "ABCD-1234"
        assert captured["browser"] == "https://github.com/login/device"
        assert "secret" not in str(manager.status())
        with pytest.raises(ValueError, match="already running"):
            await manager.start("codex")
        release.set()
        await manager.task
        assert manager.status()["state"] == "completed"
        assert "device_code" not in manager.status()
        assert captured["terminated"]
        assert "--with-token" not in captured["args"]
    asyncio.run(run())


@pytest.mark.parametrize("cancel", [True, False])
def test_cancel_and_timeout_stop_process_tree(monkeypatch, cancel):
    async def run():
        cleaned = []
        class Output:
            async def read(self, size): await asyncio.Event().wait()
        stdin = SimpleNamespace(close=lambda: None)
        async def create(*args, **kwargs): return SimpleNamespace(stdout=Output(), stdin=stdin)
        async def terminate(*args, **kwargs): cleaned.append(True)
        monkeypatch.setattr("app.agent_login.create_managed_process", create)
        monkeypatch.setattr("app.agent_login.terminate_process_tree", terminate)
        manager = AgentLogin()
        manager.TTL_SECONDS = .02
        await manager.start("codex")
        await asyncio.sleep(.001)
        if cancel: await manager.cancel()
        else: await manager.task
        assert manager.status()["state"] == ("cancelled" if cancel else "expired")
        assert cleaned == [True]
        assert AgentLogin.command("codex")[1:3] == ["-c", 'cli_auth_credentials_store="keyring"']
    asyncio.run(run())


def test_refresh_preserves_verified_agy_but_invalid_check_disables(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fixture.sqlite")
    db.init_db()
    monkeypatch.setattr("app.registry.shutil.which", lambda name: "/fixture/agy" if name == "agy" else None)
    monkeypatch.setattr(AgentRegistry, "_version", staticmethod(lambda command: "fixture"))
    registry = AgentRegistry()
    registry.refresh()
    monkeypatch.setattr(AgentRegistry, "_probe_credentials", classmethod(lambda cls, agent: ("valid", "OK", {})))
    assert registry.check_token("antigravity-cli")["available"]
    registry.refresh()
    assert registry.get("antigravity-cli")["available"]
    monkeypatch.setattr(AgentRegistry, "_probe_credentials", classmethod(lambda cls, agent: ("invalid", "failed", {})))
    assert not registry.check_token("antigravity-cli")["available"]


def test_codex_discovery_never_returns_raw_auth_output(monkeypatch):
    monkeypatch.setattr("app.registry.subprocess.run", lambda *args, **kwargs:
        SimpleNamespace(returncode=0, stdout="private-token-value", stderr=""))
    assert AgentRegistry._auth_status("/fixture/codex") == (True, "CLI reports signed in")


def test_login_endpoints_require_local_operator_token(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr("app.main._local_api_token", lambda: "fixture-operator")
    client = TestClient(app)
    assert client.post("/api/connections/github/login").status_code == 401
    assert client.get("/api/connections/login").status_code == 401
    headers = {"Authorization": "Bearer fixture-operator"}
    assert client.post("/api/connections/unknown/login", headers=headers).status_code == 400
    assert client.post("/api/connections/github/login", headers=headers).status_code == 400
    response = client.get("/api/connections/login", headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert client.delete("/api/connections/login", headers=headers).status_code == 200
