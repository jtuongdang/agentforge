from __future__ import annotations

import subprocess
import json

import pytest

from fastapi.testclient import TestClient

from app import db
from app.main import app, bootstrap
from app.planner import TaskPlanner
from app.validation import ValidationEngine, check_scope, check_secrets, in_scope, scopes_may_overlap
from app.registry import AgentRegistry
from app.git_ops import task_diff


def authenticated_test_client(monkeypatch):
    monkeypatch.setattr("app.main._local_api_token", lambda: "fixture-local-api-token")
    return TestClient(app, headers={"Authorization": "Bearer fixture-local-api-token",
                                    "Origin": "http://127.0.0.1:5173"})


def test_local_api_requires_token_and_limits_session_bootstrap_origin(monkeypatch):
    monkeypatch.setattr("app.main._local_api_token", lambda: "fixture-local-api-token")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 401
        assert client.get("/api/session").status_code == 403
        session = client.get("/api/session", headers={"Origin": "http://127.0.0.1:5173"})
        assert session.status_code == 200
        assert session.json()["access_token"] == "fixture-local-api-token"
        preflight = client.options("/api/fleet", headers={"Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "PATCH", "Access-Control-Request-Headers": "authorization,content-type"})
        assert preflight.status_code == 200
        assert "PATCH" in preflight.headers["access-control-allow-methods"]
        assert client.get("/api/ws-ticket").status_code == 401
        assert client.get("/api/ws-ticket", headers={"Authorization": "Bearer fixture-local-api-token",
            "Origin": "http://127.0.0.1:5173"}).status_code == 200


def test_websocket_rejects_missing_or_unissued_ticket():
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/ws/tasks/unknown/events?ticket=not-issued",
                                         headers={"Origin": "http://127.0.0.1:5173"}):
                pass
        assert closed.value.code == 4401


def test_websocket_reconnect_replays_events_from_last_cursor(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ws-cursor.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    db.init_db()
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path) VALUES('r','fixture',?)", (str(tmp_path),))
        con.execute("""INSERT INTO tasks(id,title,objective,repository_id,file_globs)
            VALUES('task','fixture task','fixture objective','r','[\"**/*\"]')""")
    db.event("task", "first", "First event")
    db.event("task", "second", "Second event")
    db.event("task", "third", "Third event")
    with db.connect() as con:
        cursor = con.execute("SELECT id FROM events WHERE task_id='task' ORDER BY id").fetchall()[0][0]

    with authenticated_test_client(monkeypatch) as client:
        ticket = client.get("/api/ws-ticket").json()["ticket"]
        with client.websocket_connect(f"/ws/tasks/task/events?after={cursor}&ticket={ticket}",
                                     headers={"Origin": "http://127.0.0.1:5173"}) as socket:
            second = socket.receive_json()
        assert second["kind"] == "second"

        ticket = client.get("/api/ws-ticket").json()["ticket"]
        with client.websocket_connect(f"/ws/tasks/task/events?after={second['id']}&ticket={ticket}",
                                     headers={"Origin": "http://127.0.0.1:5173"}) as socket:
            third = socket.receive_json()
        assert third["kind"] == "third"


def test_tech_lead_pairing_scopes_lease_heartbeat_and_revoke(tmp_path, monkeypatch):
    from app import tech_lead

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "lead.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    db.init_db()
    pairing = tech_lead.create_pairing(["handover:read", "task:create", "task:review"])
    session = tech_lead.handshake(pairing["pairing_code"], "client-1", "Fixture Lead")
    assert session["access_token"]
    assert tech_lead.authorize(session["access_token"], "task:create")["id"] == session["session_id"]
    assert tech_lead.authorize(session["access_token"], "task:publish") is None
    assert tech_lead.status()["connected"] is True
    assert tech_lead.heartbeat(session["access_token"])["session_id"] == session["session_id"]

    with db.connect() as con:
        row = con.execute("SELECT token_hash,state FROM lead_sessions WHERE id=?", (session["session_id"],)).fetchone()
    assert row["token_hash"] != session["access_token"]
    assert row["state"] == "active"
    with pytest.raises(RuntimeError):
        other = tech_lead.create_pairing(["handover:read"])
        tech_lead.handshake(other["pairing_code"], "client-2", "Other Lead")
    with pytest.raises(ValueError):
        tech_lead.create_pairing(["task:publish"])

    assert tech_lead.revoke(session["session_id"]) is True
    assert tech_lead.status()["connected"] is False
    assert tech_lead.authorize(session["access_token"]) is None


def test_tech_lead_api_uses_separate_scoped_token_and_audits_reviews(tmp_path, monkeypatch):
    from app import tech_lead

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "lead-api.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    db.init_db()
    repo = tmp_path / "lead-fixture"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    (repo / "README.md").write_text("fixture", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-m", "initial"], check=True, capture_output=True)
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path,default_branch) VALUES('r','fixture',?,'main')", (str(repo),))

    client = authenticated_test_client(monkeypatch)
    pairing = client.post("/api/lead/pairing", json={"scopes": ["handover:read", "task:create", "task:review"]})
    assert pairing.status_code == 201
    handshake = client.post("/api/lead/handshake", json={"pairing_code": pairing.json()["pairing_code"],
        "client_id": "fixture-client", "client_name": "Fixture Lead"})
    assert handshake.status_code == 201
    lead_token = handshake.json()["access_token"]
    lead_headers = {"Authorization": f"Bearer {lead_token}"}
    assert client.get("/api/lead/client/handover", headers=lead_headers).status_code == 200
    assert client.get("/api/fleet", headers=lead_headers).status_code == 401

    created = client.post("/api/lead/client/tasks", headers=lead_headers, json={
        "title": "Scoped lead task", "objective": "Create a fixture task under lead scope.", "repository_id": "r",
        "file_globs": ["README.md"], "required_capabilities": ["implementation"]})
    assert created.status_code == 202, created.text
    task_id = created.json()["id"]
    review = client.post(f"/api/lead/client/tasks/{task_id}/reviews", headers=lead_headers,
                         json={"decision": "request_changes", "comments": "Clarify expected behavior."})
    assert review.status_code == 201
    assert client.get(f"/api/tasks/{task_id}/reviews").json()[0]["decision"] == "request_changes"

    revoked = client.post(f"/api/lead/sessions/{handshake.json()['session_id']}/revoke")
    assert revoked.status_code == 200
    assert client.post("/api/lead/client/heartbeat", headers=lead_headers).status_code == 401


def test_task_planner_keeps_the_declared_file_boundary():
    plan = TaskPlanner().plan("Small task", "Implement one UI label", ["clinic-desktop/src/**/*.java"], ["java"],
                              ["The label is visible to a user."],
                              "## Mandatory project guidance\nUse the documented naming rules.",
                              ["docs/PROJECT_CONVENTIONS.md"])
    assert plan.title == "Small task"
    assert plan.file_globs == ["clinic-desktop/src/**/*.java"]
    assert "Allowed file globs" in plan.objective
    assert "synthetic data only" in plan.objective
    assert "The label is visible to a user." in plan.objective
    assert "Mandatory project guidance" in plan.objective
    assert "docs/PROJECT_CONVENTIONS.md" in plan.objective
    assert "Completion report must list guidance files read." in plan.objective


def test_project_guidance_loads_root_and_scoped_agent_instructions(tmp_path):
    from app.project_guidance import load_project_guidance

    (tmp_path / "AGENTS.md").write_text("Root rules", encoding="utf-8")
    module = tmp_path / "clinic-domain"
    module.mkdir()
    (module / "AGENTS.md").write_text("Domain rules", encoding="utf-8")
    (tmp_path / "README.md").write_text("Project description", encoding="utf-8")

    guidance, files = load_project_guidance(tmp_path, ["clinic-domain/src/**/*.java"])

    assert "Orchestrator baseline: docs/PROJECT_CONVENTIONS.md" in files
    assert "AGENTS.md" in files
    assert "clinic-domain/AGENTS.md" in files
    assert "README.md" in files
    assert "Root rules" in guidance
    assert "Domain rules" in guidance
    assert "mandatory" in guidance.lower()


def test_scope_check_handles_nested_globs_and_blocks_unlisted_paths():
    assert in_scope("clinic-application/src/main/Handler.java", ["clinic-application/src/**"])
    assert check_scope(["clinic-application/src/Handler.java", "README.md"], ["clinic-application/src/**"]) == ["README.md"]
    assert not in_scope("../outside.txt", ["**/*"])


def test_parallel_file_scope_check_is_conservative_and_allows_separate_module_roots():
    assert not scopes_may_overlap(["clinic-domain/src/**"], ["clinic-application/src/**"])
    assert scopes_may_overlap(["clinic-application/src/**"], ["clinic-application/src/main/**"])
    assert scopes_may_overlap(["**/*.java"], ["docs/**/*.md"])


def test_contracts_and_task_creation_are_persisted_in_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "orchestrator.sqlite3")
    db.init_db()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "--allow-empty", "-m", "initial"], check=True, capture_output=True)

    with authenticated_test_client(monkeypatch) as client:
        registered = client.post("/api/repositories", json={"name": "Fixture", "path": str(repo), "default_branch": "main"})
        assert registered.status_code == 201, registered.text
        contract = client.post("/api/contracts", json={"title": "DTO boundary", "body": "The DTO contains an id and display name."})
        assert contract.status_code == 201, contract.text
        task = client.post("/api/tasks", json={"title": "Document contract consumer", "objective": "Update the consumer to use the shared display field.",
            "repository_id": registered.json()["id"], "required_capabilities": ["fixture-only-capability"],
            "file_globs": ["docs/*.md"], "acceptance_criteria": ["Consumer compiles with the shared display field."],
            "priority": "high", "dependencies": [], "contract_id": contract.json()["id"]})
        assert task.status_code == 202, task.text
        assert task.json()["status"] == "queued"
        assert task.json()["acceptance_criteria"] == ["Consumer compiles with the shared display field."]
        assert task.json()["priority"] == "high"
        saved = client.get("/api/tasks").json()
        assert saved[0]["id"] == task.json()["id"]
        approved = client.post(f"/api/contracts/{contract.json()['id']}/approve")
        assert approved.status_code == 200


def test_no_remote_task_can_be_completed_locally_without_pushing(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "local-complete.sqlite3")
    monkeypatch.setattr("app.main.registry.refresh", lambda: [])
    repo = tmp_path / "local-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    (repo / "tracked.md").write_text("validated\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-m", "initial"], check=True, capture_output=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()

    with authenticated_test_client(monkeypatch) as client:
        registered = client.post("/api/repositories", json={"name": "Local only", "path": str(repo), "default_branch": "main"})
        task = client.post("/api/tasks", json={"title": "Local task", "objective": "Save the completed work locally.",
            "repository_id": registered.json()["id"], "required_capabilities": ["no-fixture-agent"],
            "file_globs": ["docs/*.md"]})
        assert task.status_code == 202
        with db.transaction() as con:
            con.execute("UPDATE tasks SET status='awaiting_pr_approval',branch='agent/task-test',worktree=?,validation=? WHERE id=?",
                (str(repo), db.dumps({"passed": True, "scope_passed": True, "commit": head}), task.json()["id"]))
        (repo / "after-validation.txt").write_text("changed\n", encoding="utf-8")
        stale = client.post(f"/api/tasks/{task.json()['id']}/complete-local")
        assert stale.status_code == 409
        (repo / "after-validation.txt").unlink()
        completed = client.post(f"/api/tasks/{task.json()['id']}/complete-local")
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"
        assert completed.json()["pr_url"] is None


def test_sqlite_transaction_rolls_back_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "rollback.sqlite3")
    db.init_db()
    try:
        with db.transaction() as con:
            con.execute("INSERT INTO contracts(id,title,body) VALUES('c1','temporary','synthetic')")
            raise RuntimeError("rollback fixture")
    except RuntimeError:
        pass
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 0


def test_sqlite_snapshot_backup_verify_and_restore_to_new_file(tmp_path):
    from scripts.db_snapshot import backup_database, integrity_check, restore_database

    source = tmp_path / "active.sqlite3"
    with __import__("sqlite3").connect(source) as con:
        con.execute("CREATE TABLE marker(value TEXT)")
        con.execute("INSERT INTO marker VALUES('synthetic')")
    snapshot = tmp_path / "backups" / "snapshot.sqlite3"
    restored = tmp_path / "restored.sqlite3"

    backup_database(source, snapshot)
    integrity_check(snapshot)
    restore_database(snapshot, restored)

    with __import__("sqlite3").connect(restored) as con:
        assert con.execute("SELECT value FROM marker").fetchone()[0] == "synthetic"
    try:
        restore_database(snapshot, restored)
    except FileExistsError:
        pass
    else:
        raise AssertionError("Restore must refuse to overwrite an existing database")


def test_health_reports_database_scheduler_and_agents(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "health.sqlite3")
    monkeypatch.setattr("app.main.registry.refresh", lambda: [])
    with authenticated_test_client(monkeypatch) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["database"] == {"ok": True, "check": "ok"}
    assert "agents" in response.json()


def test_storage_endpoint_reports_sqlite_worktrees_and_record_counts(tmp_path, monkeypatch):
    from app import storage as storage_metrics

    data_root = tmp_path / "data"
    monkeypatch.setattr(storage_metrics, "DATA_ROOT", data_root)
    monkeypatch.setattr(db, "DB_PATH", data_root / "orchestrator.sqlite3")
    monkeypatch.setattr("app.main.registry.refresh", lambda: [])
    data_root.mkdir(parents=True)
    db.init_db()
    (data_root / "worktrees" / "fixture" / "task-a").mkdir(parents=True)
    (data_root / "worktrees" / "fixture" / "task-a" / "build.bin").write_bytes(b"build-output")
    (data_root / "handoffs").mkdir()
    (data_root / "handoffs" / "task.md").write_text("handoff", encoding="utf-8")

    with authenticated_test_client(monkeypatch) as client:
        response = client.get("/api/storage")

    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["categories"]["worktrees"] == len(b"build-output")
    assert snapshot["categories"]["handoffs"] == len("handoff")
    assert snapshot["categories"]["database"] > 0
    assert snapshot["data_bytes"] == sum(snapshot["categories"].values())
    assert snapshot["counts"]["tasks"] == 0
    assert snapshot["disk"]["free_bytes"] > 0


def test_bootstrap_does_not_assume_or_register_a_default_product_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "generic-orchestrator.sqlite3")
    monkeypatch.setattr("app.main.registry.refresh", lambda: [])
    bootstrap()
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM repositories").fetchone()[0] == 0


def test_setup_preflight_reports_dirty_repo_base_tools_and_git_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "setup.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "app-data")
    monkeypatch.setattr("app.main.registry.refresh", lambda: [])
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Repo Owner"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "owner@example.invalid"], check=True)
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, capture_output=True)
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()
    (repo / "uncommitted.txt").write_text("left behind", encoding="utf-8")

    with authenticated_test_client(monkeypatch) as client:
        status = client.get("/api/setup/status")
        assert status.status_code == 200
        assert status.json()["needs_onboarding"] is True
        response = client.post("/api/repositories/preflight", json={"path": str(repo), "default_branch": "main",
            "validation_profile": "python", "validation_command": ["missing-fixture-validator"]})

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["repository_root"] == str(repo)
    assert report["base_sha"] == base
    assert report["working_tree_dirty"] is True
    assert report["dirty_base_note"]
    assert report["git_author"] == {"name": "Repo Owner", "email": "owner@example.invalid"}
    assert report["validator_available"] is False


def test_git_author_prefers_repository_identity_over_global(tmp_path, monkeypatch):
    from app.git_ops import git_author

    global_config = tmp_path / "gitconfig"
    subprocess.run(["git", "config", "--file", str(global_config), "user.name", "Global Person"], check=True)
    subprocess.run(["git", "config", "--file", str(global_config), "user.email", "global@example.invalid"], check=True)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    repo = tmp_path / "identity-repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    assert git_author(str(repo)) == ("Global Person", "global@example.invalid")
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Repository Person"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "repo@example.invalid"], check=True)

    assert git_author(str(repo)) == ("Repository Person", "repo@example.invalid")


def test_db_schema_migration_is_versioned_and_backed_up(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "migration.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "app-data")
    db.init_db()
    with db.transaction() as con:
        con.execute("ALTER TABLE tasks DROP COLUMN base_sha")
        con.execute("PRAGMA user_version=0")

    db.init_db()

    with db.connect() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 6
        assert "base_sha" in {row[1] for row in con.execute("PRAGMA table_info(tasks)")}
    backups = list((tmp_path / "backups").glob("pre-migration-v0-*.sqlite3"))
    assert len(backups) == 1
    with __import__("sqlite3").connect(backups[0]) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert "base_sha" not in {row[1] for row in con.execute("PRAGMA table_info(tasks)")}


def test_writer_lease_migration_backs_up_version_three_database(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "migration-v4.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "app-data")
    db.init_db()
    with db.transaction() as con:
        con.execute("DROP TABLE task_writer_leases")
        con.execute("DROP TABLE credential_cooldowns")
        con.execute("PRAGMA user_version=3")

    db.init_db()

    backups = list((tmp_path / "backups").glob("pre-migration-v3-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                           "AND name='task_writer_leases'").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                           "AND name='credential_cooldowns'").fetchone()[0] == 0
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                            "AND name='task_writer_leases'").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                            "AND name='credential_cooldowns'").fetchone()[0] == 1


def test_provider_api_key_stays_in_keyring_and_model_check_is_bounded(tmp_path, monkeypatch):
    from app import providers

    class FakeKeyring:
        def __init__(self):
            self.values = {}

        def set_password(self, service, username, password):
            self.values[(service, username)] = password

        def get_password(self, service, username):
            return self.values.get((service, username))

        def delete_password(self, service, username):
            self.values.pop((service, username), None)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return b'{"data":[{"id":"gpt-fixture"}]}'

    class FakeOpener:
        def open(self, request, timeout):
            assert timeout == providers.TIMEOUT_SECONDS
            assert request.full_url == providers.PROVIDER_ADAPTERS["openai"].model_endpoint("")
            assert request.get_header("Authorization") == "Bearer sk-fixture-secret"
            return FakeResponse()

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "provider.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    db.init_db()
    keyring = FakeKeyring()
    monkeypatch.setattr(providers, "_keyring", lambda: keyring)
    monkeypatch.setattr(providers.urllib.request, "build_opener", lambda *_: FakeOpener())

    connection = providers.create_connection("openai", "Fixture OpenAI", api_key="sk-fixture-secret")
    assert connection["has_credential"] is True
    assert "secret_ref" not in connection
    assert "api_key" not in connection
    checked = providers.check_connection(connection["id"])
    assert checked["state"] == "valid"
    assert checked["models"] == ["gpt-fixture"]
    with db.connect() as con:
        stored = con.execute("SELECT * FROM provider_connections WHERE id=?", (connection["id"],)).fetchone()
    assert "sk-fixture-secret" not in repr(dict(stored))
    assert keyring.get_password(providers.SERVICE_NAME, stored["secret_ref"]) == "sk-fixture-secret"


def test_provider_catalog_adapters_use_provider_specific_auth_and_models(tmp_path, monkeypatch):
    from app import providers

    class FakeKeyring:
        values = {}
        def set_password(self, service, username, password): self.values[(service, username)] = password
        def get_password(self, service, username): return self.values.get((service, username))
        def delete_password(self, service, username): self.values.pop((service, username), None)

    fixtures = {
        "openai": (b'{"data":[{"id":"gpt-test"}]}', "gpt-test"),
        "anthropic": (b'{"data":[{"id":"claude-test"}]}', "claude-test"),
        "gemini": (b'{"models":[{"name":"models/gemini-test","supportedGenerationMethods":["generateContent"]}]}', "gemini-test"),
        "ollama": (b'{"models":[{"name":"llama-test"}]}', "llama-test"),
    }

    class FakeResponse:
        def __init__(self, raw): self.raw = raw
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, limit):
            assert limit == providers.MAX_RESPONSE_BYTES + 1
            return self.raw

    class FakeOpener:
        def open(self, request, timeout):
            assert timeout == providers.TIMEOUT_SECONDS
            headers = {key.lower(): value for key, value in request.header_items()}
            if "/api/tags" in request.full_url:
                assert request.full_url == "http://127.0.0.1:11434/api/tags"
                assert "authorization" not in headers
                return FakeResponse(fixtures["ollama"][0])
            provider = next(key for key, adapter in providers.PROVIDER_ADAPTERS.items()
                            if key != "ollama" and request.full_url == adapter.model_endpoint(""))
            assert headers.get("accept") == "application/json"
            if provider == "openai": assert headers["authorization"] == "Bearer fixture-key"
            if provider == "anthropic":
                assert headers["x-api-key"] == "fixture-key"
                assert headers["anthropic-version"] == "2023-06-01"
            if provider == "gemini":
                assert headers["x-goog-api-key"] == "fixture-key"
                assert "fixture-key" not in request.full_url
            return FakeResponse(fixtures[provider][0])

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "provider-catalog.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    db.init_db()
    monkeypatch.setattr(providers, "_keyring", lambda: FakeKeyring())
    monkeypatch.setattr(providers.urllib.request, "build_opener", lambda *_: FakeOpener())

    for provider in ("openai", "anthropic", "gemini"):
        connection = providers.create_connection(provider, provider, api_key="fixture-key")
        checked = providers.check_connection(connection["id"])
        assert checked["state"] == "valid"
        assert checked["models"] == [fixtures[provider][1]]
    ollama = providers.create_connection("ollama", "Local Ollama")
    checked = providers.check_connection(ollama["id"])
    assert checked["models"] == [fixtures["ollama"][1]]


def test_provider_api_requires_local_token_and_never_returns_secret(tmp_path, monkeypatch):
    from app import providers

    class FakeKeyring:
        values = {}
        def set_password(self, service, username, password): self.values[(service, username)] = password
        def get_password(self, service, username): return self.values.get((service, username))
        def delete_password(self, service, username): self.values.pop((service, username), None)

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "provider-api.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(providers, "_keyring", lambda: FakeKeyring())
    db.init_db()
    with authenticated_test_client(monkeypatch) as client:
        unauthenticated = client.get("/api/providers", headers={"Authorization": ""})
        assert unauthenticated.status_code == 401
        response = client.post("/api/providers", json={"provider":"openai", "name":"Fixture", "api_key":"sk-never-return-this"})
        assert response.status_code == 201
        assert "sk-never-return-this" not in response.text
        assert response.json()["has_credential"] is True
        listed = client.get("/api/providers")
        assert listed.status_code == 200
        assert "sk-never-return-this" not in listed.text


def test_ollama_provider_rejects_non_loopback_endpoint():
    from app.providers import ProviderError, create_connection

    with pytest.raises(ProviderError, match="local loopback"):
        create_connection("ollama", "Untrusted Ollama", endpoint="http://192.168.1.20:11434")


def test_provider_credential_store_rejects_non_os_keyring(monkeypatch):
    import keyring
    from app import providers

    class InsecureBackend:
        priority = 1

    InsecureBackend.__module__ = "keyrings.alt.file"
    monkeypatch.setattr(providers.sys, "platform", "freebsd")
    monkeypatch.setattr(keyring, "get_keyring", lambda: InsecureBackend())
    with pytest.raises(providers.ProviderError, match="not an approved OS keychain"):
        providers._keyring()


def test_provider_schema_upgrade_snapshots_version_five_database(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "migration-v5.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "app-data")
    db.init_db()
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path) VALUES('keep','keep',?)", (str(tmp_path),))
        con.execute("DROP TABLE provider_connections")
        con.execute("PRAGMA user_version=5")

    db.init_db()

    backups = list((tmp_path / "backups").glob("pre-migration-v5-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT id FROM repositories WHERE id='keep'").fetchone()[0] == "keep"
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                           "AND name='provider_connections'").fetchone()[0] == 0
    with db.connect() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 6
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                           "AND name='provider_connections'").fetchone()[0] == 1


def test_packaged_data_directory_is_outside_application_bundle(tmp_path, monkeypatch):
    from pathlib import Path
    from app import settings as settings_module

    monkeypatch.delenv("CLINIC_ORCHESTRATOR_DATA_DIR", raising=False)
    monkeypatch.setattr(settings_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(settings_module.sys, "platform", "darwin")

    data_dir = settings_module.default_data_dir()

    assert data_dir == Path.home() / "Library" / "Application Support" / "DevAgent Orchestrator"
    assert not data_dir.is_relative_to(settings_module.ROOT)


def test_repository_validation_profiles_require_reviewable_argv_for_non_maven():
    from pydantic import ValidationError
    from app.models import RepositoryCreate

    with pytest.raises(ValidationError):
        RepositoryCreate(name="Python", path="/tmp/project", validation_profile="python")
    configured = RepositoryCreate(name="Python", path="/tmp/project", validation_profile="python",
                                 validation_command=["python3", "-m", "pytest"])
    assert configured.validation_command == ["python3", "-m", "pytest"]


def test_restart_reconciliation_marks_running_and_publishing_tasks_for_manual_review(tmp_path, monkeypatch):
    from app.main import reconcile_interrupted_tasks

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "restart.sqlite3")
    db.init_db()
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path,default_branch) VALUES('r','fixture',?,'main')", (str(tmp_path),))
        con.execute("INSERT INTO agents(id,name,adapter,command,capabilities,file_globs) VALUES('a','Agent','exec','[]','[]','[\"**/*\"]')")
        con.execute("INSERT INTO tasks(id,title,objective,repository_id,status,assigned_agent_id,file_globs) VALUES('t1','run','objective','r','running','a','[\"**/*\"]')")
        con.execute("INSERT INTO tasks(id,title,objective,repository_id,status,assigned_agent_id,file_globs) VALUES('t2','publish','objective','r','publishing','a','[\"**/*\"]')")
        con.execute("INSERT INTO runs(id,task_id,agent_id,state) VALUES('run1','t1','a','running')")
        con.execute("INSERT INTO task_writer_leases(task_id,lease_id,run_id,agent_id,state) VALUES('t1','lease1','run1','a','active')")

    reconcile_interrupted_tasks()

    with db.connect() as con:
        assert con.execute("SELECT status FROM tasks WHERE id='t1'").fetchone()[0] == "failed"
        assert con.execute("SELECT status FROM tasks WHERE id='t2'").fetchone()[0] == "blocked"
        assert con.execute("SELECT state FROM runs WHERE id='run1'").fetchone()[0] == "interrupted"
        assert con.execute("SELECT state FROM task_writer_leases WHERE task_id='t1'").fetchone()[0] == "orphaned"
        assert con.execute("SELECT COUNT(*) FROM events WHERE kind='restart_recovery'").fetchone()[0] == 2


def test_restart_recovery_scans_tracked_and_untracked_checkpoint_before_marking_blocked(tmp_path, monkeypatch):
    import json
    from app.main import reconcile_interrupted_tasks

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "restart-scan.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    db.init_db()
    worktree = tmp_path / "retained-worktree"
    (worktree / "src").mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(worktree)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.email", "fixture@example.invalid"], check=True)
    tracked = worktree / "src" / "main.py"
    tracked.write_text("original = True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(worktree), "add", "src/main.py"], check=True)
    subprocess.run(["git", "-C", str(worktree), "commit", "-m", "base"], check=True,
                   capture_output=True)
    tracked.write_text("changed = True\n", encoding="utf-8")
    untracked = worktree / "src" / "settings.py"
    untracked.write_text("token=ghp_abcdefghijklmnopqrstuvwxyz123456\n", encoding="utf-8")
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path,default_branch) VALUES('r','fixture',?,'main')",
                    (str(tmp_path),))
        con.execute("INSERT INTO agents(id,name,adapter,command,capabilities,file_globs) "
                    "VALUES('a','Agent','exec','[]','[]','[\"**/*\"]')")
        con.execute("""INSERT INTO tasks(id,title,objective,repository_id,base_branch,base_sha,status,
            assigned_agent_id,file_globs,worktree) VALUES('t','task','objective','r','main',NULL,'running',
            'a','["src/*.py"]',?)""", (str(worktree),))
        con.execute("INSERT INTO runs(id,task_id,agent_id,state) VALUES('run','t','a','running')")
        con.execute("INSERT INTO task_writer_leases(task_id,lease_id,run_id,agent_id,state) "
                    "VALUES('t','lease','run','a','active')")

    reconcile_interrupted_tasks()

    with db.connect() as con:
        task = con.execute("SELECT status FROM tasks WHERE id='t'").fetchone()
        report = json.loads(con.execute("SELECT report FROM runs WHERE id='run'").fetchone()[0])
    assert task["status"] == "blocked"
    assert report["checkpoint"]["scanned"] is True
    assert report["checkpoint"]["changed_paths"] == ["src/main.py", "src/settings.py"]
    assert report["checkpoint"]["possible_secret_files"] == ["src/settings.py"]


def test_writer_lease_excludes_parallel_writer_and_requires_current_owner_for_transfer(tmp_path, monkeypatch):
    from app.writer_leases import WriterLeaseManager

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "writer-lease.sqlite3")
    db.init_db()
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path) VALUES('r','fixture',?)", (str(tmp_path),))
        con.execute("INSERT INTO agents(id,name,adapter,command,capabilities,file_globs) VALUES"
                    "('a','Agent A','exec','[]','[]','[]'),('b','Agent B','exec','[]','[]','[]')")
        con.execute("INSERT INTO tasks(id,title,objective,repository_id,file_globs) "
                    "VALUES('t','task','objective','r','[]')")
        con.execute("INSERT INTO runs(id,task_id,agent_id,state) VALUES"
                    "('run-a','t','a','running'),('run-b','t','b','running')")

    assert WriterLeaseManager.acquire("t", "run-a", "a", "lease-a") is True
    assert WriterLeaseManager.acquire("t", "run-b", "b", "lease-b") is False
    from app.reporting import ReportingManager
    task_snapshot = ReportingManager().tasks()[0]
    assert task_snapshot["writer_lease_state"] == "active"
    assert task_snapshot["writer_lease_run_id"] == "run-a"
    assert WriterLeaseManager.transfer("t", "lease-a", "run-b", "a", "run-b", "b") is False
    assert WriterLeaseManager.transfer("t", "lease-a", "run-a", "a", "run-b", "b") is True
    assert WriterLeaseManager.get("t")["agent_id"] == "b"
    assert WriterLeaseManager.release("t", "lease-a") is True
    assert WriterLeaseManager.release("t", "lease-a") is False


def test_rescue_worker_transfer_respects_credential_budget_and_moves_run_provenance(tmp_path, monkeypatch):
    from app.fleet import transfer_task_to_rescue, update_settings

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "rescue-budget.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    db.init_db()
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path) VALUES('r','fixture',?)", (str(tmp_path),))
        con.execute("INSERT INTO agents(id,name,adapter,command,capabilities,file_globs,max_parallel,enabled,available) VALUES"
                    "('a','Agent A','exec','[\"/bin/a\"]','[]','[\"**/*\"]',1,1,1),"
                    "('b','Agent B','exec','[\"/bin/b\"]','[]','[\"**/*\"]',1,1,1),"
                    "('c','Agent C','exec','[\"/bin/b\"]','[]','[\"**/*\"]',1,1,1)")
        con.execute("INSERT INTO tasks(id,title,objective,repository_id,status,assigned_agent_id,file_globs) VALUES"
                    "('t','task','objective','r','recovering','a','[\"**/*\"]'),"
                    "('other','other','objective','r','running','c','[\"**/*\"]')")
        con.execute("INSERT INTO runs(id,task_id,agent_id,state) VALUES"
                    "('run-a','t','a','failed'),('other-run','other','b','running')")
        con.execute("INSERT INTO task_writer_leases(task_id,lease_id,run_id,agent_id,state) "
                    "VALUES('t','lease-a','run-a','a','active')")
    update_settings({"max_per_credential": 1})

    assert not transfer_task_to_rescue("t", "a", {"id": "b", "adapter": "exec", "command": ["/bin/b"]},
                                       "lease-a", "run-a", "run-b")
    with db.transaction() as con:
        con.execute("UPDATE tasks SET status='completed' WHERE id='other'")
        con.execute("INSERT INTO tasks(id,title,objective,repository_id,status,assigned_agent_id,file_globs) "
                    "VALUES('active-b','active','objective','r','running','b','[\"**/*\"]')")
    update_settings({"max_per_credential": 2})
    assert not transfer_task_to_rescue("t", "a", {"id": "b", "adapter": "exec", "command": ["/bin/b"]},
                                       "lease-a", "run-a", "run-b")
    with db.transaction() as con:
        con.execute("UPDATE tasks SET status='completed' WHERE id='active-b'")
    assert transfer_task_to_rescue("t", "a", {"id": "b", "adapter": "exec", "command": ["/bin/b"]},
                                   "lease-a", "run-a", "run-b")
    with db.connect() as con:
        assert con.execute("SELECT assigned_agent_id FROM tasks WHERE id='t'").fetchone()[0] == "b"
        assert con.execute("SELECT agent_id FROM runs WHERE id='run-b'").fetchone()[0] == "b"
        assert con.execute("SELECT run_id FROM task_writer_leases WHERE task_id='t'").fetchone()[0] == "run-b"


def test_rate_limit_cooldown_is_persisted_per_cli_credential(tmp_path, monkeypatch):
    from app.fleet import cooldown_remaining, set_credential_cooldown

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "credential-cooldown.sqlite3")
    db.init_db()
    agent = {"adapter": "exec", "command": ["/bin/worker"]}

    assert cooldown_remaining(agent) == 0
    set_credential_cooldown(agent, 15, "rate_limit")

    assert 0 < cooldown_remaining(agent) <= 15


def test_scheduler_does_not_admit_agent_during_credential_cooldown(tmp_path, monkeypatch):
    from app import fleet
    from app.scheduler import TaskScheduler

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cooldown-admission.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr("app.scheduler.disk_free_bytes", lambda: 10**12)
    db.init_db()
    agent = {"id": "agent", "adapter": "exec", "command": ["/bin/worker"]}
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path) VALUES('r','fixture',?)", (str(tmp_path),))
        con.execute("INSERT INTO tasks(id,title,objective,repository_id,file_globs) "
                    "VALUES('t','task','objective','r','[\"**/*\"]')")
    fleet.set_credential_cooldown(agent, 15, "rate_limit")

    assert TaskScheduler._claim_task("t", agent) is False
    with db.connect() as con:
        assert con.execute("SELECT status FROM tasks WHERE id='t'").fetchone()[0] == "queued"


def test_engine_moves_rate_limited_task_to_budgeted_rescue_worker(tmp_path, monkeypatch):
    import asyncio
    import sys
    from app.engine import AgentExecutionEngine
    from app.fleet import cooldown_remaining

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "engine-rescue.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "app-data")
    db.init_db()
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    worktree = tmp_path / "task-worktree"
    worktree.mkdir()
    (worktree / "src").mkdir()
    primary_cli = tmp_path / "primary-worker"
    rescue_cli = tmp_path / "rescue-worker"
    primary_cli.write_text(f"#!{sys.executable}\nimport sys\nprint('HTTP 429 Too Many Requests', flush=True)\nsys.exit(1)\n",
                           encoding="utf-8")
    rescue_cli.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath('src/app.py').write_text('ready = True\\n')\nprint('rescue complete')\n",
                          encoding="utf-8")
    primary_cli.chmod(0o755)
    rescue_cli.chmod(0o755)
    primary_command = [str(primary_cli)]
    rescue_command = [str(rescue_cli)]
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path,default_branch) VALUES('r','fixture',?,'main')",
                    (str(repo_path),))
        con.execute("INSERT INTO agents(id,name,adapter,command,capabilities,file_globs,max_parallel,enabled,available) VALUES"
                    "('a','Primary','exec',?,'[\"implementation\"]','[\"**/*\"]',1,1,1),"
                    "('b','Rescue','exec',?,'[\"implementation\"]','[\"**/*\"]',1,1,1)",
                    (db.dumps(primary_command), db.dumps(rescue_command)))
        con.execute("""INSERT INTO tasks(id,title,objective,repository_id,base_branch,status,
            required_capabilities,file_globs,max_retries) VALUES('t','implement','do work','r','main','queued',
            '["implementation"]','["**/*"]',1)""")

    class FakeWorktrees:
        def prepare(self, *_args):
            return "orchestrator/t", str(worktree)
        def changed_paths(self, *_args):
            return ["src/app.py"]
        def commit(self, *_args):
            return "abc123"

    class FakeValidation:
        @staticmethod
        def inspect_scope(*_args):
            return []
        @staticmethod
        def inspect_secrets(*_args):
            return []
        async def validate(self, *_args, **_kwargs):
            return {"passed": True}

    primary = {"id": "a", "name": "Primary", "adapter": "exec", "command": primary_command,
               "capabilities": ["implementation"], "file_globs": ["**/*"], "enabled": True, "available": True}
    rescue = {"id": "b", "name": "Rescue", "adapter": "exec", "command": rescue_command,
              "capabilities": ["implementation"], "file_globs": ["**/*"], "enabled": True, "available": True}
    class FakeRegistry:
        @staticmethod
        def list():
            return [primary, rescue]

    engine = AgentExecutionEngine(FakeRegistry(), FakeWorktrees(), FakeValidation())
    asyncio.run(engine.execute("t", primary))

    with db.connect() as con:
        task = con.execute("SELECT status,assigned_agent_id,worktree FROM tasks WHERE id='t'").fetchone()
        runs = con.execute("SELECT agent_id,state FROM runs WHERE task_id='t' ORDER BY started_at,rowid").fetchall()
        lease_count = con.execute("SELECT COUNT(*) FROM task_writer_leases WHERE task_id='t'").fetchone()[0]
        transfer = con.execute("SELECT details FROM events WHERE task_id='t' AND kind='worker_rescue_transfer'").fetchone()
    assert task["status"] == "awaiting_pr_approval"
    assert task["assigned_agent_id"] == "b"
    assert task["worktree"] == str(worktree)
    assert [(row["agent_id"], row["state"]) for row in runs] == [("a", "failed"), ("b", "passed")]
    assert lease_count == 0
    assert transfer is not None
    assert cooldown_remaining(primary) > 0


def test_validation_engine_blocks_unsupported_project_instead_of_running_maven(tmp_path):
    import asyncio

    result = asyncio.run(ValidationEngine().validate(str(tmp_path), lambda *_: None))
    assert result["blocked"] is True
    assert result["profile"] == "unsupported"
    assert "root pom.xml" in result["summary"]


def test_docs_folder_does_not_skip_code_validation(tmp_path):
    import asyncio

    result = asyncio.run(ValidationEngine().validate(str(tmp_path), lambda *_: None,
        profile="auto", paths=["docs/runtime.py"]))

    assert result["blocked"] is True
    assert result["profile"] == "unsupported"


def test_custom_validator_runs_for_documentation_changes(tmp_path):
    import asyncio
    import sys

    result = asyncio.run(ValidationEngine().validate(str(tmp_path), lambda *_: None,
        profile="custom", command=[sys.executable, "-c", "raise SystemExit(7)"], paths=["README.md"]))

    assert result["passed"] is False
    assert result["exit_code"] == 7


def test_secret_scanner_ignores_credential_getters_but_detects_literal_values(tmp_path):
    source = tmp_path / "Auth.java"
    source.write_text(
        "char[] password = command.passwordCopy();\n"
        "char[] password = txtPassword.getPassword();\n"
        'String password = "synthetic-fixture-value-123";\n'
        "token=syntheticfixturetoken123456;\n",
        encoding="utf-8",
    )

    assert check_secrets(str(tmp_path), ["Auth.java"]) == ["Auth.java"]


def test_secret_scanner_allows_safe_credential_access_expressions(tmp_path):
    source = tmp_path / "Login.java"
    source.write_text(
        "char[] password = command.passwordCopy();\n"
        "char[] password = txtPassword.getPassword();\n"
        "String credential = passwordHasher.hash(password);\n",
        encoding="utf-8",
    )

    assert check_secrets(str(tmp_path), ["Login.java"]) == []


def test_custom_validator_uses_argv_without_shell_and_redacts_output(tmp_path):
    import asyncio
    import sys

    result = asyncio.run(ValidationEngine().validate(
        str(tmp_path), lambda *_: None, "custom",
        [sys.executable, "-c", "print('token=ghp_abcdefghijklmnopqrstuvwxyz1234567890')"]))
    assert result["passed"] is True
    assert result["command"] == "configured project command"
    assert "ghp_" not in result["tail"]
    assert "[REDACTED]" in result["tail"]


def test_task_diff_returns_patch_for_a_specific_commit(tmp_path):
    repo = tmp_path / "diff-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    (repo / "note.md").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "note.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-m", "initial"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "task"], check=True, capture_output=True)
    (repo / "note.md").write_text("after\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-am", "task change"],
                   check=True, capture_output=True, env={**__import__("os").environ, "GIT_AUTHOR_NAME": "Fixture",
                   "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_NAME": "Fixture",
                   "GIT_COMMITTER_EMAIL": "fixture@example.invalid"})
    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                            capture_output=True, text=True).stdout.strip()

    preview = task_diff(str(repo), commit, base_branch="main")

    assert "-before" in preview["diff"]
    assert "+after" in preview["diff"]
    assert preview["truncated"] is False
    first = task_diff(str(repo), commit, max_chars=10, base_branch="main")
    second = task_diff(str(repo), commit, max_chars=1000, base_branch="main", offset=first["next_offset"])
    assert first["truncated"] is True
    assert first["diff"] + second["diff"] == preview["diff"]
    assert second["next_offset"] is None


def test_changed_paths_includes_all_commits_and_uncommitted_files(tmp_path):
    from app.git_ops import changed_paths

    repo = tmp_path / "changeset-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
    (repo / "base.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "task"], check=True, capture_output=True)
    (repo / "first.py").write_text("first = True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "first.py"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "first task commit"], check=True, capture_output=True)
    (repo / "second.md").write_text("second\n", encoding="utf-8")

    assert changed_paths(str(repo), base) == ["first.py", "second.md"]


def test_github_remote_detection_rejects_non_github_hosts():
    from app.git_ops import is_github_remote

    assert is_github_remote("https://github.com/org/repo.git")
    assert is_github_remote("git@github.com:org/repo.git")
    assert not is_github_remote("https://gitlab.com/org/repo.git")
    assert not is_github_remote("https://evil.example/github.com/org/repo.git")


def test_scheduler_queues_urgent_tasks_before_normal_tasks(tmp_path, monkeypatch):
    from app.main import scheduler

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "priority.sqlite3")
    db.init_db()
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path) VALUES('r','fixture',?)", (str(tmp_path),))
        for task_id, priority in (("low", "low"), ("normal", "normal"), ("high", "high"), ("urgent", "urgent")):
            con.execute("INSERT INTO tasks(id,title,objective,repository_id,file_globs,priority) VALUES(?,?,?,?,?,?)",
                        (task_id, task_id, "fixture objective", "r", '["**/*"]', priority))

    assert [task["id"] for task in scheduler._queued_tasks()] == ["urgent", "high", "normal", "low"]


def test_scheduler_atomically_claims_task_only_once_across_instances(tmp_path, monkeypatch):
    from app.scheduler import TaskScheduler

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "claim.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    db.init_db()
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path) VALUES('r','fixture',?)", (str(tmp_path),))
        con.execute("INSERT INTO tasks(id,title,objective,repository_id,file_globs,status) VALUES(?,?,?,?,?,?)",
                    ("task", "task", "fixture objective", "r", '["docs/*.md"]', "queued"))

    # Separate scheduler objects share SQLite but have no shared in-memory active-task map.
    assert TaskScheduler._claim_task("task") is True
    assert TaskScheduler._claim_task("task") is False
    with db.connect() as con:
        assert con.execute("SELECT status FROM tasks WHERE id='task'").fetchone()[0] == "planning"


def test_fleet_capacity_persists_and_scheduler_drains_without_cancelling_runs(tmp_path, monkeypatch):
    from app import fleet
    from app.main import fleet_status

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fleet.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    db.init_db()
    fleet.update_settings({"max_active_runs": 1, "max_parallel_builds": 1, "max_per_credential": 1,
                           "min_free_disk_bytes": 256 * 1024**2,
                           "disk_reserve_per_run_bytes": 64 * 1024**2})
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path) VALUES('r','fixture',?)", (str(tmp_path),))
        for task_id in ("active-a", "active-b"):
            con.execute("""INSERT INTO tasks(id,title,objective,repository_id,file_globs,status)
                VALUES(?,?,?,?,?,'running')""", (task_id, task_id, "fixture objective", "r", '["**/*"]'))

    view = fleet_status()
    assert view["active_run_count"] == 2
    assert view["draining"] is True
    assert {run["task_id"] for run in view["active_runs"]} == {"active-a", "active-b"}
    assert {row[0] for row in _task_statuses()} == {"running"}

    fleet.update_settings({"max_active_runs": 3})
    db.init_db()
    assert fleet.settings()["max_active_runs"] == 3
    assert fleet_status()["draining"] is False


def test_fleet_api_reads_and_updates_persistent_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fleet-api.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    db.init_db()
    with authenticated_test_client(monkeypatch) as client:
        initial = client.get("/api/fleet")
        assert initial.status_code == 200
        assert initial.json()["settings"]["max_active_runs"] == 2
        updated = client.patch("/api/fleet", json={"max_active_runs": 1, "max_parallel_builds": 1})
        assert updated.status_code == 200
        assert updated.json()["settings"]["max_active_runs"] == 1
        assert updated.json()["settings"]["max_parallel_builds"] == 1


def _task_statuses():
    with db.connect() as con:
        return con.execute("SELECT status FROM tasks").fetchall()


def test_scheduler_enforces_global_run_and_shared_credential_caps(tmp_path, monkeypatch):
    from app import fleet
    from app.scheduler import TaskScheduler

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fleet-claim.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(fleet, "disk_free_bytes", lambda: 10**12)
    db.init_db()
    fleet.update_settings({"max_active_runs": 2, "max_per_credential": 1})
    command = db.dumps(["/opt/test-agent"])
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path) VALUES('r','fixture',?)", (str(tmp_path),))
        con.execute("INSERT INTO agents(id,name,adapter,command) VALUES('a1','Agent one','codex',?)", (command,))
        con.execute("INSERT INTO agents(id,name,adapter,command) VALUES('a2','Agent two','codex',?)", (command,))
        con.execute("""INSERT INTO tasks(id,title,objective,repository_id,file_globs,status,assigned_agent_id)
            VALUES('active','active','fixture objective','r','[\"**/*\"]','running','a1')""")
        con.execute("""INSERT INTO tasks(id,title,objective,repository_id,file_globs)
            VALUES('queued','queued','fixture objective','r','[\"**/*\"]')""")

    assert TaskScheduler._claim_task("queued", {"id": "a2", "adapter": "codex", "command": ["/opt/test-agent"]}) is False
    with db.connect() as con:
        assert con.execute("SELECT status FROM tasks WHERE id='queued'").fetchone()[0] == "queued"


def test_parallel_scheduler_claims_never_exceed_global_run_budget(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app import fleet
    from app.scheduler import TaskScheduler

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "parallel-claims.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr("app.scheduler.disk_free_bytes", lambda: 10**12)
    db.init_db()
    fleet.update_settings({"max_active_runs": 1})
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path) VALUES('r','fixture',?)", (str(tmp_path),))
        for index in range(4):
            con.execute("INSERT INTO tasks(id,title,objective,repository_id,file_globs) VALUES(?,?,?,?,?)",
                        (f"queued-{index}", "queued", "fixture objective", "r", '[\"**/*\"]'))

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(TaskScheduler._claim_task, (f"queued-{index}" for index in range(4))))
    assert sum(claims) == 1
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM tasks WHERE status='planning'").fetchone()[0] == 1


def test_scheduler_reserves_disk_before_claiming_new_run(tmp_path, monkeypatch):
    from app import fleet
    from app.scheduler import TaskScheduler

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "disk-claim.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr("app.scheduler.disk_free_bytes", lambda: 300 * 1024**2)
    db.init_db()
    fleet.update_settings({"min_free_disk_bytes": 256 * 1024**2, "disk_reserve_per_run_bytes": 64 * 1024**2})
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path) VALUES('r','fixture',?)", (str(tmp_path),))
        con.execute("INSERT INTO tasks(id,title,objective,repository_id,file_globs) VALUES('queued','queued','fixture objective','r','[\"**/*\"]')")

    assert TaskScheduler._claim_task("queued") is False
    with db.connect() as con:
        assert con.execute("SELECT status FROM tasks WHERE id='queued'").fetchone()[0] == "queued"


def test_build_slots_hot_apply_capacity_without_releasing_active_build(tmp_path, monkeypatch):
    import asyncio
    from app import fleet

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "build-slots.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    db.init_db()
    fleet.update_settings({"max_parallel_builds": 1})
    gate = fleet.BuildSlotGate()

    async def exercise():
        await gate.acquire()
        waiting = asyncio.create_task(gate.acquire())
        await asyncio.sleep(0.25)
        assert not waiting.done()
        fleet.update_settings({"max_parallel_builds": 2})
        await asyncio.wait_for(waiting, timeout=1)
        assert gate.active == 2
        gate.release()
        gate.release()
        assert gate.active == 0

    asyncio.run(exercise())


def test_successful_validation_clears_stale_task_error(tmp_path, monkeypatch):
    from app.engine import set_task

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "clear-error.sqlite3")
    db.init_db()
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path) VALUES('r','fixture',?)", (str(tmp_path),))
        con.execute("INSERT INTO tasks(id,title,objective,repository_id,file_globs,status,error) VALUES(?,?,?,?,?,?,?)",
                    ("task", "task", "fixture objective", "r", '["docs/*.md"]', "validating", "stale branch race"))

    set_task("task", "awaiting_pr_approval", validation={"passed": True}, error=None)
    with db.connect() as con:
        row = con.execute("SELECT status,error FROM tasks WHERE id='task'").fetchone()
    assert row[0] == "awaiting_pr_approval"
    assert row[1] is None


def test_agent_credential_check_persists_status_without_raw_cli_output(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "credential.sqlite3")
    db.init_db()
    registry = AgentRegistry()
    agent = registry.register({"id": "codex-fixture", "name": "Codex fixture", "adapter": "codex",
        "command": ["/fixture/codex"], "capabilities": ["testing"], "file_globs": ["**/*"], "max_parallel": 1})
    assert agent["credential_status"] == "unchecked"

    class Result:
        returncode = 0
        stdout = "Logged in; secret-token-value"
        stderr = ""

    monkeypatch.setattr("app.registry.subprocess.run", lambda *args, **kwargs: Result())
    checked = registry.check_token("codex-fixture")

    assert checked["credential_status"] == "valid"
    assert checked["credential_checked_at"]
    assert "secret-token-value" not in str(checked)
    with db.connect() as con:
        row = con.execute("SELECT * FROM agents WHERE id='codex-fixture'").fetchone()
    assert "secret-token-value" not in str(dict(row))


def test_agent_soft_removal_survives_refresh_and_can_be_restored(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "disabled-agent.sqlite3")
    db.init_db()
    registry = AgentRegistry()
    registry.register({"id": "codex-local", "name": "Codex CLI", "adapter": "codex",
        "command": ["/fixture/codex"], "capabilities": ["testing"], "file_globs": ["**/*"], "max_parallel": 1})
    assert registry.set_enabled("codex-local", False)["enabled"] is False
    monkeypatch.setattr("app.registry.shutil.which", lambda name: "/fixture/codex" if name == "codex" else None)
    monkeypatch.setattr(AgentRegistry, "_version", staticmethod(lambda command: "fixture version"))
    monkeypatch.setattr(AgentRegistry, "_auth_status", staticmethod(lambda executable: (True, "signed in")))

    after_refresh = {agent["id"]: agent for agent in registry.refresh()}["codex-local"]
    assert after_refresh["enabled"] is False
    assert registry.set_enabled("codex-local", True)["enabled"] is True


def test_antigravity_quota_parser_keeps_only_safe_usage_fields():
    payload = '{"command":{"data":{"groups":[{"name":"Gemini Models","buckets":[{"name":"Weekly Limit Remaining","remaining_fraction":0.72,"reset_time":"2099-01-01","token":"secret"}]}]}}}'
    usage = AgentRegistry._antigravity_usage(payload)
    assert usage == {"groups": [{"name": "Gemini Models", "label": "Weekly Limit Remaining", "remaining_percent": 72, "reset_at": "2099-01-01"}]}
    assert "secret" not in str(usage)


def test_agent_api_check_and_remove_keep_agent_history(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "agent-api.sqlite3")
    monkeypatch.setattr("app.main.registry.refresh", lambda: [])
    db.init_db()
    registry = AgentRegistry()
    registry.register({"id": "codex-api-fixture", "name": "Codex API fixture", "adapter": "codex",
        "command": ["/fixture/codex"], "capabilities": ["testing"], "file_globs": ["**/*"], "max_parallel": 1})

    class Result:
        returncode = 0
        stdout = "Logged in"
        stderr = ""

    monkeypatch.setattr("app.registry.subprocess.run", lambda *args, **kwargs: Result())
    with authenticated_test_client(monkeypatch) as client:
        checked = client.post("/api/agents/codex-api-fixture/check-token")
        assert checked.status_code == 200
        assert checked.json()["credential_status"] == "valid"
        removed = client.delete("/api/agents/codex-api-fixture")
        assert removed.status_code == 200
        assert removed.json()["enabled"] is False
        with db.connect() as con:
            assert con.execute("SELECT COUNT(*) FROM agents WHERE id='codex-api-fixture'").fetchone()[0] == 1
        restored = client.post("/api/agents/codex-api-fixture/restore")
        assert restored.status_code == 200
        assert restored.json()["enabled"] is True


def test_registry_discovers_authenticated_copilot_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.sqlite3")
    db.init_db()
    monkeypatch.setattr("app.registry.shutil.which", lambda name: f"/usr/local/bin/{name}" if name in {"codex", "copilot"} else None)
    monkeypatch.setattr(AgentRegistry, "_version", staticmethod(lambda command: "fixture-cli 1.0"))
    monkeypatch.setattr(AgentRegistry, "_auth_status", staticmethod(lambda executable: (True, "signed in")))

    agents = AgentRegistry().refresh()

    by_id = {agent["id"]: agent for agent in agents}
    assert by_id["codex-local"]["available"] is True
    assert by_id["copilot-cli"]["adapter"] == "copilot"
    assert by_id["copilot-cli"]["available"] is True
    assert by_id["copilot-cli"]["max_parallel"] == 1


def test_registry_discovers_antigravity_cli_in_its_default_install_location(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry-agy.sqlite3")
    db.init_db()
    monkeypatch.setattr("app.registry.shutil.which", lambda name: None)
    monkeypatch.setattr("app.registry.Path.home", lambda: tmp_path)
    agy = tmp_path / ".local" / "bin" / "agy"
    agy.parent.mkdir(parents=True)
    agy.write_text("synthetic executable marker")
    monkeypatch.setattr(AgentRegistry, "_version", staticmethod(lambda command: "1.5.0"))

    agents = AgentRegistry().refresh()

    by_id = {agent["id"]: agent for agent in agents}
    assert by_id["antigravity-cli"]["adapter"] == "antigravity"
    assert by_id["antigravity-cli"]["command"] == [str(agy)]
    assert by_id["antigravity-cli"]["available"] is True
    assert by_id["antigravity-ide"]["adapter"] == "handoff"


def test_copilot_adapter_runs_noninteractive_without_trusting_repo(tmp_path, monkeypatch):
    import asyncio
    from app.engine import AgentExecutionEngine

    captured = {}

    class FakeOutput:
        async def readline(self):
            return b""

    class FakeProcess:
        stdout = FakeOutput()
        returncode = 0

        async def wait(self):
            return 0

    async def fake_create(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    async def run_adapter():
        engine = object.__new__(AgentExecutionEngine)
        monkeypatch.setattr("app.engine.asyncio.create_subprocess_exec", fake_create)
        monkeypatch.setattr("app.engine.log_event", lambda *args, **kwargs: None)
        return await engine._invoke({"adapter": "copilot", "command": ["/usr/local/bin/copilot"], "id": "copilot-cli"},
                                    str(tmp_path), tmp_path / "TASK.md", "synthetic prompt", "task-fixture")

    assert asyncio.run(run_adapter()) == 0
    args = captured["args"]
    assert args[0] == "/usr/local/bin/copilot"
    assert "--prompt" in args and "synthetic prompt" in args
    assert "--allow-all-tools" in args
    assert "--disable-builtin-mcps" in args
    assert "--disable-client-telemetry" in args
    assert "--trust-folder" not in args
    assert "--allow-all-paths" not in args
    assert captured["kwargs"]["cwd"] == str(tmp_path)


def test_antigravity_adapter_uses_headless_sandbox_and_accept_edits(tmp_path, monkeypatch):
    import asyncio
    from pathlib import Path
    from app.engine import AgentExecutionEngine

    captured = {}

    class FakeOutput:
        async def readline(self):
            return b""

    class FakeProcess:
        stdout = FakeOutput()
        returncode = 0

        async def wait(self):
            return 0

    async def fake_create(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    async def run_adapter():
        engine = object.__new__(AgentExecutionEngine)
        monkeypatch.setattr("app.engine.asyncio.create_subprocess_exec", fake_create)
        monkeypatch.setattr("app.engine.log_event", lambda *args, **kwargs: None)
        return await engine._invoke({"adapter": "antigravity", "command": ["/usr/local/bin/agy"], "id": "antigravity-cli"},
                                    str(tmp_path), tmp_path / "TASK.md", "synthetic prompt", "task-fixture",
                                    ["src/main/java/vn/example/Allowed.java"])

    assert asyncio.run(run_adapter()) == 0
    args = captured["args"]
    assert args[0] == "/usr/local/bin/agy"
    assert "--print" in args and any("synthetic prompt" in argument for argument in args)
    agent_prompt = args[args.index("--print") + 1]
    assert "requires absolute paths" in agent_prompt
    assert str(tmp_path.resolve()) in agent_prompt
    guidance_path = Path(__file__).resolve().parents[2] / "docs" / "PROJECT_CONVENTIONS.md"
    assert str(guidance_path) in agent_prompt
    assert "--output-format" in args and "stream-json" in args
    assert "--mode=accept-edits" in args
    assert "--dangerously-skip-permissions" not in args
    assert "--sandbox" in args
    settings = json.loads(args[args.index("--settings") + 1])
    assert settings["permissions"]["allow"] == [
        f"read_file({tmp_path.resolve()}/**)",
        f"read_file({guidance_path})",
        f"write_file({(tmp_path / 'src/main/java/vn/example/Allowed.java').resolve()})",
    ]
    assert settings["permissions"]["deny"] == ["mcp(*)", "read_url(*)", "execute_url(*)", "command(*)"]
    assert captured["kwargs"]["cwd"] == str(tmp_path)


def test_antigravity_write_permissions_reject_path_traversal(tmp_path):
    from app.engine import AgentExecutionEngine

    with pytest.raises(RuntimeError, match="unsafe file glob"):
        AgentExecutionEngine._antigravity_write_permissions(str(tmp_path), ["../outside.txt"])


def test_recovery_classifies_auth_rate_limit_timeout_and_blocked_failures():
    from app.recovery import RecoveryManager

    assert RecoveryManager.classify(1, ["HTTP 401 invalid API key"]).category == "auth"
    limited = RecoveryManager.classify(1, ["429 Too Many Requests", "Retry-After: 12"])
    assert limited.category == "rate_limit"
    assert limited.retryable is True
    assert limited.retry_after_seconds == 12
    assert RecoveryManager.classify(124).category == "timeout"
    blocked = RecoveryManager.classify(0, validation={"blocked": True, "reason": "possible_secret"})
    assert blocked.category == "blocked"
    assert blocked.retryable is False


def test_recovery_backoff_is_bounded_and_honors_retry_after():
    from app.recovery import RecoveryManager

    assert RecoveryManager.backoff_seconds(0) == 1
    assert RecoveryManager.backoff_seconds(3) == 8
    assert RecoveryManager.backoff_seconds(5, 50) == 50
    assert RecoveryManager.backoff_seconds(9, 500) == 60


def test_antigravity_write_permissions_reject_symlink_escape(tmp_path):
    from app.engine import AgentExecutionEngine

    outside = tmp_path.parent / "outside-antigravity-scope"
    outside.mkdir(exist_ok=True)
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="outside its worktree"):
        AgentExecutionEngine._antigravity_write_permissions(str(tmp_path), ["escape/new.txt"])


def test_agent_event_summary_reports_tool_permission_path_without_file_contents():
    from app.engine import AgentExecutionEngine

    line = json.dumps({
        "event": "step_update",
        "step_update": {
            "step_type": "tool",
            "tool_name": "read_file",
            "tool_info": {
                "parameters": {"Path": "/workspace/src/Example.java"},
                "error": {"message": "permission denied"},
                "output": "secret source text",
            },
        },
    })
    summary = AgentExecutionEngine._event_summary(line)
    assert "/workspace/src/Example.java" in summary
    assert "permission denied" in summary
    assert "secret source text" not in summary


def test_prune_worktrees_preserves_active_tasks_and_removes_old(tmp_path, monkeypatch):
    from app.worktrees import prune_worktrees

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "prune-test.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    db.init_db()

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    (repo_dir / "README.md").write_text("root")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

    repo_id = "repo-1"
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path) VALUES(?,?,?)", (repo_id, "Repo 1", str(repo_dir)))

    wt_active = tmp_path / "data" / "worktrees" / repo_id / "task-active"
    wt_old = tmp_path / "data" / "worktrees" / repo_id / "task-old"

    subprocess.run(["git", "worktree", "add", "-b", "branch-active", str(wt_active), "main"], cwd=repo_dir, check=True)
    subprocess.run(["git", "worktree", "add", "-b", "branch-old", str(wt_old), "main"], cwd=repo_dir, check=True)
    subprocess.run(["git", "merge", "--no-ff", "branch-old", "-m", "integrate old task"], cwd=repo_dir,
                   check=True, capture_output=True)

    with db.transaction() as con:
        con.execute("""INSERT INTO tasks(id,title,objective,file_globs,repository_id,status,worktree)
                       VALUES(?,?,?,?,?,?,?)""",
                    ("task-active", "Active Task", "Obj", "[]", repo_id, "running", str(wt_active)))
        con.execute("""INSERT INTO tasks(id,title,objective,file_globs,repository_id,status,worktree,branch,finished_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    ("task-old", "Old Task", "Obj", "[]", repo_id, "completed", str(wt_old), "branch-old", "2020-01-01T00:00:00+00:00"))

    # Dry run should identify old task without deleting
    dry_result = prune_worktrees(max_age_hours=24.0, dry_run=True)
    assert dry_result["total_cleaned_count"] == 1
    assert wt_old.exists()
    assert wt_active.exists()

    # Actual prune
    res = prune_worktrees(max_age_hours=24.0, dry_run=False)
    assert res["fully_removed_count"] == 1
    assert not wt_old.exists()
    assert wt_active.exists()
    assert res["remaining_count"] == 1

    with db.connect() as con:
        old_row = con.execute("SELECT worktree FROM tasks WHERE id='task-old'").fetchone()
        assert old_row["worktree"] is None


def test_prune_worktrees_preserves_unmerged_source_and_untracked_build_files(tmp_path, monkeypatch):
    from app.worktrees import prune_worktrees

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "target-test.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    db.init_db()

    repo_dir = tmp_path / "repo2"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    (repo_dir / "README.md").write_text("root")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

    repo_id = "repo-2"
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path) VALUES(?,?,?)", (repo_id, "Repo 2", str(repo_dir)))

    wt_unmerged = tmp_path / "data" / "worktrees" / repo_id / "task-unmerged"
    subprocess.run(["git", "worktree", "add", "-b", "orchestrator/unmerged-feat", str(wt_unmerged), "main"], cwd=repo_dir, check=True)

    # Add unique commit on this branch so it's NOT merged into main
    source_file = wt_unmerged / "Service.java"
    source_file.write_text("public class Service {}")
    subprocess.run(["git", "add", "Service.java"], cwd=wt_unmerged, check=True)
    subprocess.run(["git", "commit", "-m", "new feature"], cwd=wt_unmerged, check=True)

    # Add compiler artifact target folder
    target_dir = wt_unmerged / "target"
    target_dir.mkdir()
    (target_dir / "Service.class").write_bytes(b"\xca\xfe\xba\xbe")

    with db.transaction() as con:
        con.execute("""INSERT INTO tasks(id,title,objective,file_globs,repository_id,status,worktree,branch,finished_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    ("task-unmerged", "Unmerged Task", "Obj", "[]", repo_id, "completed",
                     str(wt_unmerged), "orchestrator/unmerged-feat", "2020-01-01T00:00:00+00:00"))

    res = prune_worktrees(max_age_hours=24.0, dry_run=False, clean_unmerged_targets_only=True)
    assert res["target_cleaned_count"] == 0
    assert res["fully_removed_count"] == 0

    # Source code is preserved 100%!
    assert wt_unmerged.exists()
    assert source_file.exists()
    assert source_file.read_text() == "public class Service {}"

    # Build output is untracked data too; cleanup leaves it for explicit operator review.
    assert target_dir.exists()




def test_rag_indexes_docs_incrementally_and_injects_excerpts_into_guidance(tmp_path, monkeypatch):
    from app import rag
    from app.project_guidance import load_project_guidance
    monkeypatch.setattr(rag, "RAG_DB_PATH", tmp_path / "rag.sqlite3")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "bhyt.md").write_text("# BHYT\n\nHạn thẻ bảo hiểm y tế tính đến hết ngày hết hạn.\n", encoding="utf-8")
    (docs / "other.md").write_text("# Khác\n\nNội dung không liên quan về kho thuốc.\n", encoding="utf-8")
    first = rag.index_repository("r1", tmp_path)
    assert first["indexed"] == 2
    assert rag.index_repository("r1", tmp_path)["unchanged"] == 2
    hits = rag.search("r1", "hạn thẻ bảo hiểm")
    assert hits and hits[0]["path"] == "docs/bhyt.md"
    guidance, files = load_project_guidance(tmp_path, [], "r1", "kiểm tra hạn thẻ bảo hiểm")
    assert "Relevant documentation excerpts" in guidance
    assert any("bhyt.md" in f for f in files)
    (docs / "other.md").unlink()
    assert rag.index_repository("r1", tmp_path)["removed"] == 1


def test_rag_dedupes_sections_and_prefers_decision_docs(tmp_path, monkeypatch):
    from app import rag
    monkeypatch.setattr(rag, "RAG_DB_PATH", tmp_path / "rag.sqlite3")
    docs = tmp_path / "docs"
    docs.mkdir()
    long_section = "\n\n".join(f"Audit read quyền deny đoạn {i} " + "x" * 900 for i in range(6))
    (docs / "phase2-audit-proposal.md").write_text(f"# Audit\n\n{long_section}\n", encoding="utf-8")
    (docs / "phase2-read-audit-decision.md").write_text("# Quyết định\n\nAudit read quyền deny cho mọi role.\n", encoding="utf-8")
    for i in range(8):
        (docs / f"filler{i}.md").write_text(f"# Mục {i}\n\nNội dung về kho thuốc số {i} và lịch hẹn.\n", encoding="utf-8")
    rag.index_repository("r2", tmp_path)
    hits = rag.search("r2", "audit read quyền deny", limit=5)
    assert hits[0]["path"] == "docs/phase2-read-audit-decision.md"
    assert len({(h["path"], h["heading"]) for h in hits}) == len(hits)


def test_code_context_is_revision_worktree_scoped_and_skips_secrets(tmp_path, monkeypatch):
    import sqlite3
    from app import code_context

    monkeypatch.setattr(code_context, "CONTEXT_DB_PATH", tmp_path / "context.sqlite3")
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "fixture@example.invalid"], check=True)
    source = root / "src" / "billing.py"
    source.parent.mkdir()
    source.write_text("def calculate_tax(amount):\n    return amount * 0.1\n", encoding="utf-8")
    (root / "src" / "secret_config.py").write_text(
        "token=ghp_abcdefghijklmnopqrstuvwxyz123456\n", encoding="utf-8")
    (root / "src" / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "operations.md").write_text(
        "# Worker shutdown\n\nTerminate descendants before transferring the writer lease.\n",
        encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "tax implementation"], check=True,
                   capture_output=True)
    revision_one = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                                  capture_output=True, text=True).stdout.strip()

    first = code_context.search("repo-1", root, "calculate tax", revision_one, max_chars=1000)

    assert first["revision"] == revision_one
    assert first["citations"][0]["path"] == "src/billing.py"
    assert first["citations"][0]["start_line"] == 1
    assert revision_one[:12] in first["context"]
    assert first["stats"]["skipped_secrets"] == 1
    assert first["stats"]["parse_errors"] == 1
    assert first["symbols"][0]["qualified_name"] == "calculate_tax"
    markdown = code_context.search("repo-1", root, "terminate descendants", revision_one)
    assert markdown["citations"][0]["path"] == "docs/operations.md"
    assert markdown["citations"][0]["kind"] == "markdown"

    mirror = tmp_path / "mirror"
    subprocess.run(["git", "-C", str(root), "worktree", "add", "--detach", str(mirror), revision_one],
                   check=True, capture_output=True)
    mirrored = code_context.search("repo-1", mirror, "calculate tax", revision_one)
    assert mirrored["citations"][0]["worktree"] == str(mirror)
    with sqlite3.connect(code_context.CONTEXT_DB_PATH) as con:
        indexed_worktrees = con.execute("SELECT DISTINCT worktree FROM source_files").fetchall()
    assert {row[0] for row in indexed_worktrees} == {str(root), str(mirror)}

    (root / "src" / "untracked.py").write_text("def injected(): pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="clean worktree"):
        code_context.search("repo-1", root, "injected", revision_one)
    (root / "src" / "untracked.py").unlink()
    source.write_text("def calculate_tax(amount):\n    return amount * 0.9\n", encoding="utf-8")
    with pytest.raises(ValueError, match="clean worktree"):
        code_context.search("repo-1", root, "calculate tax", revision_one)
    source.write_text("def calculate_tax(amount):\n    return amount * 0.1\n", encoding="utf-8")

    source.write_text("def calculate_discount(amount):\n    return amount * 0.2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "src/billing.py"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "discount implementation"], check=True,
                   capture_output=True)
    revision_two = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                                  capture_output=True, text=True).stdout.strip()

    second = code_context.search("repo-1", root, "calculate discount", revision_two, max_chars=600)
    old = code_context.search("repo-1", root, "calculate tax", revision_two, max_chars=600)

    assert revision_two != revision_one
    assert second["citations"] and all(hit["revision"] == revision_two for hit in second["citations"])
    assert len(second["context"]) <= 600
    assert all(hit["revision"] == revision_two for hit in old["citations"])
    assert "calculate_tax" not in old["context"]
    assert old["symbols"] == []

    renamed = root / "src" / "pricing.py"
    source.rename(renamed)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "rename billing service"], check=True,
                   capture_output=True)
    revision_three = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                                    capture_output=True, text=True).stdout.strip()
    moved = code_context.search("repo-1", root, "calculate discount", revision_three)
    assert moved["stats"]["removed"] >= 1
    assert moved["citations"] and all(hit["path"] == "src/pricing.py" for hit in moved["citations"])

    renamed.unlink()
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "remove pricing service"], check=True,
                   capture_output=True)
    revision_four = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                                   capture_output=True, text=True).stdout.strip()
    removed = code_context.search("repo-1", root, "calculate discount", revision_four)
    assert removed["stats"]["removed"] >= 1
    assert removed["citations"] == [] and removed["symbols"] == []


def test_code_context_api_returns_revision_citations_and_budget(tmp_path, monkeypatch):
    from app import code_context

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "code-context-api.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(code_context, "CONTEXT_DB_PATH", tmp_path / "context.sqlite3")
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "fixture@example.invalid"], check=True)
    (root / "service.py").write_text("def calculate_invoice_total(items):\n    return sum(items)\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "service.py"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "add invoice service"], check=True,
                   capture_output=True)
    db.init_db()
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path,default_branch) VALUES('r','fixture',?,'main')",
                    (str(root),))

    with authenticated_test_client(monkeypatch) as client:
        response = client.get("/api/repositories/r/code-context?q=invoice%20total&max_chars=512")

    assert response.status_code == 200
    body = response.json()
    assert body["citations"][0]["path"] == "service.py"
    assert body["citations"][0]["revision"] == body["revision"]
    assert len(body["context"]) <= 512


def test_prune_events_removes_old_noise_but_keeps_milestones_and_active(tmp_path, monkeypatch):
    from app import storage
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "events.sqlite3")
    db.init_db()
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path,default_branch) VALUES('r','r','/tmp/r','main')")
        for tid, status in (("done", "completed"), ("live", "running")):
            con.execute("INSERT INTO tasks(id,title,objective,repository_id,status,required_capabilities,file_globs) "
                        "VALUES(?,?,?,?,?,'[]','[]')", (tid, tid, "o", "r", status))
        old = "datetime('now','-3 days')"
        for tid in ("done", "live"):
            con.execute(f"INSERT INTO events(task_id,kind,message,details,created_at) "
                        f"VALUES(?,'agent_log','assistant.reasoning_delta: x','{{}}',{old})", (tid,))
        con.execute(f"INSERT INTO events(task_id,kind,message,details,created_at) "
                    f"VALUES('done','completed','finished','{{}}',{old})")
    result = storage.prune_events(vacuum=False)
    assert result["deleted"]["noise"] == 1
    with db.connect() as con:
        kinds = [(r["task_id"], r["kind"]) for r in con.execute("SELECT task_id,kind FROM events ORDER BY id")]
    assert kinds == [("live", "agent_log"), ("done", "completed")]


def test_scheduler_dependency_requires_commit_in_selected_base(monkeypatch):
    from app import scheduler as scheduler_module

    task = {"repository_id": "r", "base_branch": "main", "dependencies": ["d"]}
    dependency = {"repository_id": "r", "validation": {"commit": "a" * 40}}
    monkeypatch.setattr(scheduler_module, "repository", lambda _rid: {"path": "/fixture", "default_branch": "main"})
    monkeypatch.setattr(scheduler_module, "get_task", lambda _tid: dependency)
    monkeypatch.setattr(scheduler_module, "run", lambda *_args: subprocess.CompletedProcess([], 1, "", ""))
    assert scheduler_module.TaskScheduler._dependencies_integrated(task) is False
    monkeypatch.setattr(scheduler_module, "run", lambda *_args: subprocess.CompletedProcess([], 0, "", ""))
    assert scheduler_module.TaskScheduler._dependencies_integrated(task) is True


def test_terminate_process_tree_stops_child_process(tmp_path):
    import asyncio
    import os
    import sys
    import time
    from app.processes import process_group_options, terminate_process_tree

    if os.name != "posix":
        pytest.skip("Process group termination assertion currently targets POSIX")
    ready = tmp_path / "child-ready"
    marker = tmp_path / "child-survived"
    child_code = ("import pathlib,time; pathlib.Path(%r).write_text('ready'); "
                  "time.sleep(0.5); pathlib.Path(%r).write_text('survived')") % (str(ready), str(marker))
    parent_code = ("import subprocess,sys; subprocess.Popen([sys.executable,'-c',%r])") % child_code

    async def scenario():
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", parent_code, **process_group_options())
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert ready.exists(), "child failed to start"
        await process.wait()  # The group leader may exit while the descendant remains alive.
        await terminate_process_tree(process, grace_seconds=0.5)
        await asyncio.sleep(0.7)
        assert not marker.exists(), "child outlived its process group"

    asyncio.run(scenario())


def test_agent_invoke_stops_descendants_after_worker_exits(tmp_path, monkeypatch):
    import asyncio
    import os
    import sys
    from app.engine import AgentExecutionEngine

    if os.name != "posix":
        pytest.skip("Process group termination assertion currently targets POSIX")
    marker = tmp_path / "orphan-wrote-after-worker-exit"
    child_code = ("import pathlib,time; time.sleep(0.7); pathlib.Path(%r).write_text('survived')") % str(marker)
    parent_code = ("import subprocess,sys; subprocess.Popen([sys.executable,'-c',%r], "
                   "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print('done')") % child_code

    async def scenario():
        engine = object.__new__(AgentExecutionEngine)
        monkeypatch.setattr("app.engine.log_event", lambda *_args, **_kwargs: None)
        result = await engine._invoke({"adapter": "exec", "command": [sys.executable, "-c", parent_code],
                                       "id": "fixture-agent"}, str(tmp_path), tmp_path / "TASK.md",
                                      "fixture", "task-fixture")
        await asyncio.sleep(0.9)
        return result

    assert asyncio.run(scenario()) == 0
    assert not marker.exists()


def test_dashboard_static_mount_serves_spa(monkeypatch):
    with authenticated_test_client(monkeypatch) as client:
        res = client.get("/")
        assert res.status_code == 200
        assert "<!doctype html>" in res.text
        # API route still takes precedence
        api_res = client.get("/api/health")
        assert api_res.status_code == 200
        assert api_res.json()["ok"] is True


def _portable_checkpoint_fixture(tmp_path, monkeypatch):
    import json
    import subprocess
    from app import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "data" / "orchestrator.sqlite3")
    monkeypatch.setattr(db, "DATA_ROOT", tmp_path / "data")
    db.DB_PATH.parent.mkdir(parents=True)
    db.init_db()
    repo = tmp_path / "source project"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    (repo / "README.md").write_text("safe committed fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-m", "fixture"], check=True, capture_output=True)
    with db.transaction() as con:
        con.execute("INSERT INTO repositories(id,name,path,remote,default_branch) VALUES(?,?,?,?,?)",
                    ("repo-a", "Fixture", str(repo), "https://user:ghp_" + "A" * 36 + "@github.com/org/repo.git", "main"))
        con.execute("INSERT INTO agents(id,name,adapter,command,enabled) VALUES(?,?,?,?,1)",
                    ("agent-a", "CLI", "exec", json.dumps(["agent", "ghp_" + "B" * 36])))
        con.execute("INSERT INTO system_settings(key,value) VALUES('provider.api_token',?)", ("ghp_" + "C" * 36,))
        con.execute("INSERT INTO system_settings(key,value) VALUES('fleet.max_active_runs','2')")
        con.execute("INSERT INTO provider_connections(id,provider,name,model,secret_ref,state,models) "
                    "VALUES('provider-a','openai','Fixture','model-a','source-keyring-ref','valid','[\"model-a\"]')")
    return repo


def test_checkpoint_export_redacts_credentials_paths_and_disabled_agents(tmp_path, monkeypatch):
    import json
    import sqlite3
    import zipfile
    from app import portability

    assert portability._redact_remote("git@github.com:org/repo.git") == "git@github.com:org/repo.git"
    assert portability._redact_remote("alice@github.com:org/repo.git") == "github.com:org/repo.git"
    repo = _portable_checkpoint_fixture(tmp_path, monkeypatch)
    (repo / "local-only.txt").write_text("uncommitted but not secret\n", encoding="utf-8")
    target = tmp_path / "fixture.devagent"

    result = portability.create_checkpoint(target)

    assert result["repository_count"] == 1
    assert "local-only.txt" in result["omitted_uncommitted_paths"]
    summary = portability.verify_checkpoint(target)
    assert summary["repositories"][0]["refs"] >= 1
    with zipfile.ZipFile(target) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert str(repo) not in archive.read("database.sqlite3").decode("latin1")
        assert str(repo) not in json.dumps(manifest)
        all_content = b"".join(archive.read(name) for name in archive.namelist())
        for token in ("ghp_" + "A" * 36, "ghp_" + "B" * 36, "ghp_" + "C" * 36):
            assert token.encode() not in all_content
        assert b"user:ghp_" not in all_content
        assert b"[LOCAL_PATH]" not in json.dumps(manifest).encode()
        snapshot = tmp_path / "snapshot.sqlite3"
        snapshot.write_bytes(archive.read("database.sqlite3"))
    with sqlite3.connect(snapshot) as con:
        assert con.execute("SELECT path,remote,enabled,validation_command FROM repositories WHERE id='repo-a'").fetchone() == (
            "portable://repo-a", "https://github.com/org/repo.git", 0, "[]")
        assert con.execute("SELECT command,enabled,available FROM agents WHERE id='agent-a'").fetchone() == ("[]", 0, 0)
        assert con.execute("SELECT value FROM system_settings WHERE key='fleet.max_active_runs'").fetchone() == ("2",)
        assert con.execute("SELECT COUNT(*) FROM system_settings WHERE key='provider.api_token'").fetchone()[0] == 0
        assert con.execute("SELECT secret_ref,state,models,checked_at FROM provider_connections WHERE id='provider-a'").fetchone() == (
            None, "unavailable", "[]", None)


def test_checkpoint_includes_explicit_safe_uncommitted_files_and_rejects_secrets(tmp_path, monkeypatch):
    import zipfile
    from app import portability

    repo = _portable_checkpoint_fixture(tmp_path, monkeypatch)
    changed = repo / "README.md"
    changed.write_text("safe changed content\n", encoding="utf-8")
    target = tmp_path / "included.devagent"
    portability.create_checkpoint(target, include_uncommitted=True)
    with zipfile.ZipFile(target) as archive:
        assert archive.read("repositories/repo-a/working-tree/README.md") == b"safe changed content\n"

    changed.write_text("api_key = '" + "X" * 28 + "'\n", encoding="utf-8")
    rejected = tmp_path / "rejected.devagent"
    with pytest.raises(ValueError, match="Possible secrets"):
        portability.create_checkpoint(rejected, include_uncommitted=True)
    assert not rejected.exists()


def test_checkpoint_is_discarded_if_database_changes_during_export(tmp_path, monkeypatch):
    from app import db, portability

    _portable_checkpoint_fixture(tmp_path, monkeypatch)
    original_verify = portability.verify_checkpoint

    def verify_then_write(path):
        result = original_verify(path)
        with db.transaction() as con:
            con.execute("INSERT INTO system_settings(key,value) VALUES('concurrent.write','1')")
        return result

    monkeypatch.setattr(portability, "verify_checkpoint", verify_then_write)
    target = tmp_path / "concurrent.devagent"
    with pytest.raises(portability.CheckpointError, match="Application data changed during export"):
        portability.create_checkpoint(target)
    assert not target.exists()


def test_checkpoint_is_discarded_if_git_refs_change_during_export(tmp_path, monkeypatch):
    import subprocess
    from app import portability

    repo = _portable_checkpoint_fixture(tmp_path, monkeypatch)
    original_verify = portability.verify_checkpoint

    def verify_then_commit(path):
        result = original_verify(path)
        (repo / "raced.txt").write_text("committed during export\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "raced.txt"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.invalid", "commit", "-m", "racing commit"],
                       check=True, capture_output=True)
        return result

    monkeypatch.setattr(portability, "verify_checkpoint", verify_then_commit)
    target = tmp_path / "racing-ref.devagent"
    with pytest.raises(portability.CheckpointError, match="Repository files changed during export"):
        portability.create_checkpoint(target)
    assert not target.exists()


def test_checkpoint_verifier_rejects_zip_path_traversal(tmp_path):
    import zipfile
    from app.portability import CheckpointError, verify_checkpoint

    target = tmp_path / "unsafe.devagent"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("../escape.txt", "unsafe")
    with pytest.raises(CheckpointError, match="unsafe archive path"):
        verify_checkpoint(target)


def test_checkpoint_api_export_download_and_cleanup(tmp_path, monkeypatch):
    from dataclasses import replace
    from app import settings as settings_module
    from app import main as main_module

    repo = _portable_checkpoint_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "settings", replace(settings_module.settings, data_dir=tmp_path / "data"))
    with authenticated_test_client(monkeypatch) as client:
        preview = client.get("/api/storage/checkpoint-preview")
        assert preview.status_code == 200
        assert str(repo) not in preview.text
        assert "fingerprint" not in preview.json()["repositories"][0]
        assert "refs" not in preview.json()["repositories"][0]
        created = client.post("/api/storage/checkpoints", json={"include_uncommitted": False})
        assert created.status_code == 200
        checkpoint_id = created.json()["id"]
        download = client.get(f"/api/storage/checkpoints/{checkpoint_id}")
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/vnd.devagent.checkpoint"
        assert download.content[:2] == b"PK"
        assert not (tmp_path / "data" / "checkpoints" / f"{checkpoint_id}.devagent").exists()


def test_checkpoint_restore_remaps_repositories_and_reconciles_worktrees(tmp_path, monkeypatch):
    import sqlite3
    import subprocess
    from app import db, portability

    repo = _portable_checkpoint_fixture(tmp_path, monkeypatch)
    worktree = tmp_path / "task worktree"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree), "main"],
                   check=True, capture_output=True)
    (repo / "README.md").write_text("safe main overlay\n", encoding="utf-8")
    (worktree / "README.md").write_text("safe task overlay\n", encoding="utf-8")
    with db.transaction() as con:
        con.execute("INSERT INTO agents(id,name,adapter,command) VALUES('agent-task','CLI','exec','[]')")
        con.execute("INSERT INTO tasks(id,title,objective,repository_id,file_globs,status,branch,worktree,"
                    "assigned_agent_id) VALUES(?,?,?,?,?,?,?,?,?)",
                    ("task-a", "Task", "Restore a safe checkpoint", "repo-a", '["**/*"]',
                     "completed", "main", str(worktree), "agent-task"))

    checkpoint = tmp_path / "restore-source.devagent"
    portability.create_checkpoint(checkpoint, include_uncommitted=True)
    restored = portability.restore_checkpoint(checkpoint, tmp_path / "restored-profile")

    repo_path = tmp_path / "restored-profile" / "repositories" / "repo-a"
    worktree_path = tmp_path / "restored-profile" / "worktrees" / "task-a"
    assert restored["repository_count"] == 1
    assert restored["worktree_count"] == 1
    assert (repo_path / "README.md").read_text(encoding="utf-8") == "safe main overlay\n"
    assert (worktree_path / "README.md").read_text(encoding="utf-8") == "safe task overlay\n"
    assert subprocess.run(["git", "-C", str(repo_path), "remote", "get-url", "origin"],
                           check=True, capture_output=True, text=True).stdout.strip() == "https://github.com/org/repo.git"
    with sqlite3.connect(restored["database_path"]) as con:
        repo_row = con.execute("SELECT path,enabled FROM repositories WHERE id='repo-a'").fetchone()
        task_row = con.execute("SELECT status,worktree,error FROM tasks WHERE id='task-a'").fetchone()
        agent_row = con.execute("SELECT command,enabled FROM agents WHERE id='agent-task'").fetchone()
    assert repo_row == (str(repo_path.resolve()), 1)
    assert task_row[0] == "blocked"
    assert task_row[1] == str(worktree_path.resolve())
    assert "reconcile before retry" in task_row[2]
    assert agent_row == ("[]", 0)


def test_checkpoint_restore_refuses_existing_destination(tmp_path, monkeypatch):
    from app import portability

    _portable_checkpoint_fixture(tmp_path, monkeypatch)
    checkpoint = tmp_path / "restore-source.devagent"
    portability.create_checkpoint(checkpoint)
    existing = tmp_path / "existing-profile"
    existing.mkdir()
    original = existing / "keep.txt"
    original.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        portability.restore_checkpoint(checkpoint, existing)
    assert original.read_text(encoding="utf-8") == "preserve"


def test_checkpoint_restore_does_not_run_global_git_filters(tmp_path, monkeypatch):
    import subprocess
    from app import portability

    repo = _portable_checkpoint_fixture(tmp_path, monkeypatch)
    (repo / ".gitattributes").write_text("*.txt filter=restore-test\n", encoding="utf-8")
    (repo / "payload.txt").write_text("safe payload\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".gitattributes", "payload.txt"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "-m", "filter fixture"],
                   check=True, capture_output=True)
    marker = tmp_path / "global-filter-ran"
    global_config = tmp_path / "gitconfig"
    global_config.write_text(f'[filter "restore-test"]\n smudge = touch "{marker}"\n required = false\n',
                             encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    checkpoint = tmp_path / "filter-source.devagent"
    portability.create_checkpoint(checkpoint)
    portability.restore_checkpoint(checkpoint, tmp_path / "filter-restored")
    assert not marker.exists()


def test_checkpoint_restore_api_creates_side_by_side_profile(tmp_path, monkeypatch):
    from dataclasses import replace
    from pathlib import Path
    from app import main as main_module
    from app import portability, settings as settings_module

    _portable_checkpoint_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "settings", replace(settings_module.settings, data_dir=tmp_path / "data"))
    checkpoint = tmp_path / "api-restore.devagent"
    portability.create_checkpoint(checkpoint)
    with authenticated_test_client(monkeypatch) as client:
        response = client.post("/api/storage/checkpoints/restore", json={
            "checkpoint_path": str(checkpoint), "destination_parent": str(tmp_path)})
    assert response.status_code == 200
    result = response.json()
    assert result["repository_count"] == 1
    assert (Path(result["path"]) / "data" / "orchestrator.sqlite3").is_file()


