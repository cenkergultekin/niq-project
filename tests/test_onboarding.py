"""İzinle otomatik doldurma, DB kapsamı ve sohbetin doğru kaynağı seçmesi."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from backend.db.profiles import Profile, ProfileStore
from backend.db.sources import ConnectionGrants
from backend.routes import routes


@pytest.fixture()
def scope_api(client, tmp_path, monkeypatch):
    profiles = []
    for name, table, count in (("Satış", "orders", 3), ("CRM", "customers", 2)):
        path = tmp_path / (table + ".sqlite")
        with sqlite3.connect(path) as conn:
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
            conn.executemany(f"INSERT INTO {table} VALUES (?)", [(i,) for i in range(count)])
        profiles.append(Profile(type="sqlite", name=name, database=str(path)))
    monkeypatch.setattr(routes.state, "connector", None)
    monkeypatch.setattr(routes.state, "workspace", {})
    monkeypatch.setattr(routes.state, "profiles", ProfileStore(tmp_path / "profiles.json"))
    monkeypatch.setattr(routes.state, "connection_grants", ConnectionGrants())
    monkeypatch.setattr(routes.state, "_profile_in_background", lambda *args: None)

    class AI:
        ai_available = True
        model = "fixture"
        config = {"provider": "local"}

        def pick_databases(self, question, catalog):
            return [entry["id"] for entry in catalog if "tümü" in question or ("customers" in entry["tables"]) == ("müşteri" in question)]

        def generate_sql(self, question, schema, *args, **kwargs):
            table = next(iter(schema.tables))
            return {"sql": f"SELECT COUNT(*) AS total FROM {table}", "explanation": "Sayı"}

    monkeypatch.setattr(routes.state, "ai_service", AI())
    return client, profiles


def test_catalog_needs_permission_before_any_credentials_are_read(scope_api, monkeypatch):
    client, profiles = scope_api
    for profile in profiles:
        routes.state.profiles.save(profile)
    monkeypatch.setattr("backend.db.catalog.diagnostics.discover", lambda *args: {"local_servers": []})
    reads = []
    monkeypatch.setattr("backend.db.catalog.get_password", lambda p: reads.append(p.id) or "")
    assert client.post("/api/db/catalog", json={}).status_code == 409
    assert reads == []
    result = client.post("/api/db/catalog", json={"approved": True}).get_json()["data"]
    assert result["database_count"] == 2 and result["platform_count"] == 1
    assert all(item["recommended"] for item in result["databases"])
    assert len(reads) == 2
    assert client.post("/api/db/scope", json={"databases": [item["id"] for item in result["databases"]]}).status_code == 200


def test_catalog_only_reads_names_never_tables_or_rows(scope_api, monkeypatch):
    from backend.db.connector import Connector
    client, profiles = scope_api
    for profile in profiles:
        routes.state.profiles.save(profile)
    monkeypatch.setattr("backend.db.catalog.diagnostics.discover", lambda *args: {"local_servers": []})
    monkeypatch.setattr(Connector, "get_tables", lambda *a, **kw: pytest.fail("must not inspect tables during name discovery"))
    monkeypatch.setattr(Connector, "get_database_schema", lambda *a, **kw: pytest.fail("must not inspect columns during name discovery"))
    response = client.post("/api/db/catalog", json={"approved": True})
    assert response.status_code == 200 and response.json["data"]["database_count"] == 2
    assert all("tables" not in item and "table_count" not in item for item in response.json["data"]["databases"])


def test_first_run_gates_content_and_requires_separate_user_access(scope_api, monkeypatch):
    from app import app
    from backend.config.config import Settings
    client, profiles = scope_api
    for profile in profiles:
        routes.state.profiles.save(profile)
    monkeypatch.setitem(app.config, "ONBOARDING_REQUIRED", True)
    monkeypatch.setattr("backend.db.catalog.diagnostics.discover", lambda *args: {"local_servers": []})
    assert client.get("/api/onboarding").json["data"]["required"]
    assert client.post("/api/query", json={"question": "SELECT"}).status_code == 423
    assert client.get("/api/db/discover").status_code == 423
    assert client.get("/api/connections").status_code == 423
    assert client.post("/api/onboarding/connect", json={"databases": [], "approved": True}).status_code == 400
    assert client.post("/api/onboarding/discover", json={}).status_code == 409
    result = client.post("/api/onboarding/discover", json={"approved": True}).json["data"]
    assert routes.state.connector is None
    assert client.post("/api/query", json={"question": "still blocked"}).status_code == 423
    tokens = [item["id"] for item in result["databases"]]
    assert client.post("/api/onboarding/connect", json={"databases": tokens}).status_code == 409
    assert client.post("/api/onboarding/connect", json={"databases": ["forged"], "approved": True}).status_code == 400
    reads = []
    monkeypatch.setattr(routes.state, "_profile_in_background", lambda *a: reads.append("background-content"))
    response = client.post("/api/onboarding/connect", json={"databases": tokens[:1], "approved": True})
    assert response.status_code == 200 and response.json["data"]["scope_count"] == 1
    assert not reads and Settings.load()["onboarding_complete"]
    assert not client.get("/api/onboarding").json["data"]["required"]


def test_scope_routes_questions_without_asking_for_tables_and_history_stays_accessible(scope_api):
    client, profiles = scope_api
    tokens = [routes.state.connection_grants.issue(p, "", database_locked=True) for p in profiles]
    response = client.post("/api/db/scope", json={"databases": tokens})
    assert response.status_code == 200 and response.get_json()["data"]["scope_count"] == 2
    result = client.post("/api/query", json={"question": "Kaç müşteri var?", "mode": "query"}).get_json()
    assert result["results"] == [{"total": 2}] and result["source"] == "CRM"
    query_id = result["query_id"]
    assert client.get(f"/api/queries/{query_id}").status_code == 200
    assert client.post(f"/api/queries/{query_id}/verify", json={"verified": True}).status_code == 200
    assert client.post("/api/export", json={"query_id": query_id, "format": "csv"}).status_code == 200
    history = client.get("/api/queries").get_json()["data"]
    assert history[0]["source"] == "CRM"
    combined = client.post("/api/query", json={"question": "tümü için kayıt sayısı", "mode": "query"}).get_json()
    assert combined["success"] and combined["row_count"] == 2
    assert {row["NIQ kaynağı"] for row in combined["results"]} == {"Satış", "CRM"}
    assert client.post("/api/clear_db", json={}).status_code == 200 and not routes.state.workspace


def test_scope_only_includes_selected_databases(scope_api):
    client, profiles = scope_api
    token = routes.state.connection_grants.issue(profiles[0], "", database_locked=True)
    assert client.post("/api/db/scope", json={"databases": [token]}).status_code == 200
    assert len(routes.state.workspace) == 1
    assert next(iter(routes.state.workspace.values()))["label"] == "Satış"
    assert client.post("/api/db/scope", json={"databases": ["invalid"]}).status_code == 400
    assert len(routes.state.workspace) == 1  # Hatalı seçim mevcut kapsamı bozmadı.


def test_credential_grant_cannot_move_secret_to_another_host_or_database(scope_api):
    _, profiles = scope_api
    grants = routes.state.connection_grants
    token = grants.issue(profiles[0], "private", database_locked=True)
    with pytest.raises(ValueError):
        grants.resolve(token, {"host": "evil.example"})
    with pytest.raises(ValueError):
        grants.resolve(token, {"database": "different"})
    with pytest.raises(ValueError):
        grants.resolve(token, {"ssh_host": "different"})


def test_fifty_five_database_choices_keep_their_scoped_grants(scope_api):
    _, profiles = scope_api
    grants = routes.state.connection_grants
    tokens = [grants.issue(profiles[0], "", database_locked=True) for _ in range(55)]
    assert all(grants.resolve(token, {})[0].database == profiles[0].database for token in tokens)


def test_docker_prepare_needs_permission_and_never_returns_password(scope_api, monkeypatch):
    client, profiles = scope_api
    source = {"name": "fixture", "source_id": "fixture"}
    monkeypatch.setattr(routes, "selected_docker_source", lambda _: source)
    reads = []
    monkeypatch.setattr(routes, "read_docker_connection", lambda _: (reads.append(True) or profiles[0], "private-secret"))
    assert client.post("/api/db/prepare", json={"source_id": "fixture"}).status_code == 409 and not reads
    result = client.post("/api/db/prepare", json={"source_id": "fixture", "approved": True}).get_json()
    assert reads and result["data"]["credential_token"]
    assert "private-secret" not in json.dumps(result)


def test_docker_discovery_only_reads_public_metadata(monkeypatch):
    from backend.db import diagnostics
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ID": "a1234", "Image": "postgres:17",
            "Names": "sales", "Ports": "127.0.0.1:5555->5432/tcp"}))

    monkeypatch.setattr(diagnostics.subprocess, "run", run)
    found = diagnostics._docker_servers()
    assert found[0]["port"] == 5555 and found[0]["source_id"] == "docker:a1234:5555"
    assert calls == [["docker", "ps", "--format", "{{json .}}"]]


def test_demo_has_useful_data_without_saving_a_profile(scope_api):
    client, _ = scope_api
    response = client.post("/api/db/demo", json={})
    assert response.status_code == 200
    assert set(routes.state.connector.get_tables()) == {"customers", "products", "orders", "order_items"}
    assert routes.state.connector.execute_readonly("SELECT COUNT(*) AS n FROM orders", 1).results == [{"n": 60}]
    assert routes.state.profiles.list() == []
