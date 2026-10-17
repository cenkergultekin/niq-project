"""Arayüz dosyaları ve arayüzün dayandığı uçlar."""
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from backend.db.profiles import ProfileStore
from backend.routes import routes

STATIC = Path(__file__).resolve().parent.parent / "frontend" / "static"


def test_index_references_existing_local_assets(client):
    html = client.get("/").get_data(as_text=True)
    assets = re.findall(r'(?:src|href)="(/static/[^"?]+)', html)
    assert assets, "sayfa statik dosya yüklemiyor"
    for asset in assets:
        assert client.get(asset).status_code == 200, asset


def test_no_external_resources_so_it_works_offline():
    for path in [*STATIC.rglob("*.css"), *STATIC.joinpath("js").glob("*.js"), Path(STATIC.parent / "templates" / "index.html")]:
        text = path.read_text(encoding="utf-8")
        found = re.findall(r'(?:src|href|url\()\s*=?\s*["\']?(https?://[^"\')\s]+)', text)
        assert not found, f"{path.name} dış kaynak yüklüyor: {found}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node yok")
@pytest.mark.parametrize("name", ["core", "voice", "connect", "chat", "views"])
def test_javascript_is_syntactically_valid(name):
    result = subprocess.run(["node", "--check", str(STATIC / "js" / f"{name}.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node yok")
def test_voice_cleanup_after_errors_and_cancel():
    result = subprocess.run(["node", str(Path(__file__).parent / "voice_lifecycle.cjs")],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_all_dom_ids_used_by_scripts_exist_in_html(client):
    html = client.get("/").get_data(as_text=True)
    html_ids = set(re.findall(r'id="([^"]+)"', html))
    used = set()
    for name in ("core", "voice", "connect", "chat", "views"):
        text = (STATIC / "js" / f"{name}.js").read_text(encoding="utf-8")
        used |= set(re.findall(r"\$\('([a-z0-9_-]+)'\)", text))
        used |= set(re.findall(r"getElementById\('([a-z0-9_-]+)'\)", text))
    # dinamik olarak üretilenler hariç
    missing = sorted(i for i in used if i not in html_ids)
    assert not missing, f"HTML'de olmayan id'ler: {missing}"


@pytest.fixture()
def connected(client, tmp_path, monkeypatch):
    path = tmp_path / "ui.db"
    sqlite3.connect(path).close()
    monkeypatch.setattr(routes.state, "connector", None)
    monkeypatch.setattr(routes.state, "profiles", ProfileStore(tmp_path / "p.json"))
    monkeypatch.setattr(routes.state, "_profile_in_background", lambda profile: None)
    assert client.post("/api/set_db", json={"type": "sqlite", "database": str(path)}).status_code == 200
    return client


def test_health_includes_connected_profile_for_the_ui(connected):
    data = connected.get("/api/health").get_json()["data"]
    assert data["database_connected"] and data["profile"]["type"] == "sqlite"
    assert data["profile"]["name"] == "ui.db"  # dosya yolu değil, dosya adı
    assert "password" not in str(data["profile"]).lower().replace("has_password", "")


def test_save_current_connection_needs_approval_and_is_idempotent(connected):
    denied = connected.post("/api/connections/current", json={})
    assert denied.status_code == 409 and denied.get_json()["action"] == "save_connection"
    assert connected.get("/api/connections").get_json()["data"] == []
    assert connected.post("/api/connections/current", json={"approved": True}).status_code == 200
    assert connected.post("/api/connections/current", json={"approved": True}).status_code == 200
    assert len(connected.get("/api/connections").get_json()["data"]) == 1


def test_last_connection_survives_port_changes_but_explicit_disconnect_clears_it(connected):
    assert connected.get("/api/connections").get_json()["last_connection_id"] == ""
    saved = connected.post("/api/connections/current", json={"approved": True}).get_json()["data"]
    assert connected.get("/api/connections").get_json()["last_connection_id"] == saved["id"]
    assert connected.post("/api/clear_db", json={}).status_code == 200
    assert connected.get("/api/connections").get_json()["last_connection_id"] == ""
    assert connected.post(f"/api/connections/{saved['id']}/connect", json={}).status_code == 200
    # Başka Host portundan gelen arayüz de aynı yerel tercihi alır; localStorage'a bağlı değildir.
    response = connected.get("/api/connections", headers={"Host": "127.0.0.1:54321"}).get_json()
    assert response["last_connection_id"] == saved["id"]
