"""Bağlantı katmanı: ayrıştırma, profiller, onay, teşhis, salt-okunur çalıştırma."""
import sqlite3

import pytest

from backend.db import diagnostics
from backend.db.connector import Connector
from backend.db.dialects import DIALECTS, build_url
from backend.db.profiles import Profile, ProfileStore, parse_connection_input
from backend.routes import routes


@pytest.fixture()
def sqlite_path(tmp_path):
    path = tmp_path / "t.db"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT);"
                       "INSERT INTO items VALUES (1,'a%b'),(2,'c'),(3,'d');")
    conn.commit()
    conn.close()
    return str(path)


# --- ayrıştırma ----------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("postgresql://ali:s%40cret@db.example.com:6543/shop",
     {"type": "postgresql", "host": "db.example.com", "port": 6543, "database": "shop", "user": "ali", "password": "s@cret"}),
    ("postgres://u:p@localhost/app", {"type": "postgresql", "host": "localhost", "port": None, "database": "app"}),
    ("mysql+pymysql://root:pw@127.0.0.1:3307/sales", {"type": "mysql", "port": 3307, "database": "sales", "user": "root"}),
    ("mariadb://u:p@h/db", {"type": "mysql", "host": "h"}),
    ("jdbc:postgresql://h:5432/db", {"type": "postgresql", "host": "h", "port": 5432, "database": "db"}),
    ("jdbc:sqlserver://srv:1433;databaseName=Northwind;user=sa;password=x",
     {"type": "mssql", "host": "srv", "port": 1433, "database": "Northwind", "user": "sa", "password": "x"}),
    ("DRIVER={ODBC Driver 17 for SQL Server};SERVER=tcp:srv,1444;DATABASE=Nw;UID=u;PWD=p;",
     {"type": "mssql", "host": "srv", "port": 1444, "database": "Nw", "user": "u", "password": "p"}),
    ("Data Source=.\\SQLEXPRESS;Initial Catalog=Nw;User Id=sa;Password=pw;",
     {"type": "mssql", "host": ".\\SQLEXPRESS", "database": "Nw", "user": "sa"}),
    ("C:\\data\\shop.sqlite", {"type": "sqlite", "database": "C:\\data\\shop.sqlite"}),
    ("sqlite:///C:/data/shop.db", {"type": "sqlite", "database": "C:/data/shop.db"}),
])
def test_parse_connection_input(text, expected):
    parsed = parse_connection_input(text)
    assert parsed["recognized"]
    for key, value in expected.items():
        assert parsed.get(key) == value, key


def test_parse_unrecognized():
    assert not parse_connection_input("merhaba dünya")["recognized"]
    assert not parse_connection_input("")["recognized"]


def test_profile_defaults():
    profile = Profile(type="postgresql", database="shop")
    assert profile.host == "localhost" and profile.port == 5432 and "PostgreSQL" in profile.name
    assert Profile(type="mssql", database="x").port == 1433
    assert Profile(type="sqlite", database="/x.db").port is None
    with pytest.raises(ValueError):
        Profile(type="oracle")


def test_build_url_per_type():
    assert build_url("postgresql", "h", 5432, "db", "u", "p@ss").startswith("postgresql+psycopg://u:p%40ss@h:5432/db")
    assert build_url("mysql", "h", None, "db", "u", "p").startswith("mysql+pymysql://u:p@h:3306/db")
    assert "mode=ro" in build_url("sqlite", database="/tmp/x.db")
    assert "odbc_connect" in build_url("mssql", "srv", 1433, "db", "u", "p}x")


def test_profile_store_roundtrip(tmp_path, monkeypatch):
    removed = []
    monkeypatch.setattr("backend.db.profiles.delete_password", lambda p: removed.append(p.id))
    store = ProfileStore(tmp_path / "c.json")
    profile = store.save(Profile(type="mysql", name="Satış", database="sales", user="ro"))
    assert [p.name for p in store.list()] == ["Satış"]
    assert store.get("Satış").id == profile.id and store.get(profile.id).name == "Satış"
    assert "password" not in (tmp_path / "c.json").read_text(encoding="utf-8")
    assert store.delete("Satış").id == profile.id and removed == [profile.id] and store.list() == []


# --- gerçek sqlite ---------------------------------------------------------------
def test_connector_reads_schema_and_runs_readonly(sqlite_path):
    connector = Connector(Profile(type="sqlite", database=sqlite_path))
    assert connector.test_connection()
    assert connector.get_tables() == ["items"]
    info = connector.get_table_info("items")
    assert info.primary_key == ["id"] and info.column_names == ["id", "name"]
    result = connector.execute_readonly("SELECT name FROM items WHERE name LIKE '%b%'", 10)
    assert result.results == [{"name": "a%b"}]  # % karakteri yer tutucu sanılmadı
    limited = connector.execute_readonly("SELECT id FROM items", 2)
    assert len(limited.results) == 2 and limited.truncated
    connector.close()


def test_sqlite_is_really_read_only(sqlite_path):
    connector = Connector(Profile(type="sqlite", database=sqlite_path))
    result = connector.execute_readonly("DELETE FROM items", 10)
    assert not result.is_successful  # guard'a takılmasa bile veritabanı reddeder
    assert connector.execute_readonly("SELECT COUNT(*) AS n FROM items", 10).results == [{"n": 3}]


def test_diagnose_success_and_failure(sqlite_path, tmp_path):
    good = diagnostics.diagnose(Profile(type="sqlite", database=sqlite_path), None)
    assert good["ok"] and [s["id"] for s in good["steps"]][:3] == ["driver", "network", "login"]
    bad = diagnostics.diagnose(Profile(type="sqlite", database=str(tmp_path / "yok.db")), None)
    assert not bad["ok"]
    failing = next(s for s in bad["steps"] if s["status"] == "fail")
    assert failing["hint"]


def test_diagnose_unreachable_host_gives_hint():
    result = diagnostics.diagnose(Profile(type="postgresql", host="127.0.0.1", port=1, database="x"), "p")
    network = next(s for s in result["steps"] if s["id"] == "network")
    assert network["status"] == "fail" and "güvenlik duvarı" in network["hint"]


def test_discover_lists_drivers():
    found = diagnostics.discover()
    assert found["drivers"]["sqlite"] is True and set(found["drivers"]) == set(DIALECTS)


def test_error_hints():
    assert "parola" in diagnostics.explain_error("Login failed for user 'sa'").lower()
    assert "Veritabanı adı" in diagnostics.explain_error("Cannot open database \"x\" requested by the login")


# --- API: teşhis, ayrıştırma, onay ----------------------------------------------------
@pytest.fixture()
def api(client, tmp_path, monkeypatch):
    monkeypatch.setattr(routes.state, "profiles", ProfileStore(tmp_path / "conn.json"))
    monkeypatch.setattr(routes.state, "connector", None)
    monkeypatch.setattr("backend.routes.routes.set_password", lambda *a: None)
    monkeypatch.setattr("backend.routes.routes.delete_password", lambda *a: None)
    return client


def test_api_types_and_parse(api):
    types = api.get("/api/db/types").get_json()["data"]
    assert {t["id"] for t in types} == set(DIALECTS)
    parsed = api.post("/api/db/parse", json={"input": "postgresql://u:secret@h/db"}).get_json()["data"]
    assert parsed["type"] == "postgresql" and parsed["has_password"] and "password" not in parsed


def test_api_diagnose_with_pasted_string(api, sqlite_path):
    data = api.post("/api/db/diagnose", json={"connection_input": sqlite_path}).get_json()["data"]
    assert data["ok"]


def test_saving_profile_needs_approval_but_testing_does_not(api, sqlite_path):
    body = {"type": "sqlite", "database": sqlite_path, "name": "yerel"}
    assert api.post("/api/db/diagnose", json=body).status_code == 200  # onaysız
    denied = api.post("/api/connections", json=body)
    assert denied.status_code == 409 and denied.get_json()["action"] == "save_connection"
    assert api.get("/api/connections").get_json()["data"] == []
    assert api.post("/api/connections", json={**body, "approved": True}).status_code == 200
    saved = api.get("/api/connections").get_json()["data"]
    assert len(saved) == 1 and saved[0]["name"] == "yerel"


def test_deleting_profile_needs_approval_and_connect_saved_works(api, sqlite_path):
    api.post("/api/connections", json={"type": "sqlite", "database": sqlite_path, "name": "yerel", "approved": True})
    assert api.post("/api/connections/yerel/connect").status_code == 200
    assert api.get("/api/tables").get_json()["data"] == ["items"]
    assert api.delete("/api/connections/yerel").status_code == 409
    assert api.delete("/api/connections/yerel", json={"approved": True}).status_code == 200
    assert api.get("/api/connections").get_json()["data"] == []
