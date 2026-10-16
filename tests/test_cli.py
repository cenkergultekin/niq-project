"""Agent CLI: JSON çıktı, çıkış kodları ve onay protokolü."""
import json
import sqlite3

import pytest

import niq_cli
from backend.db.profiles import ProfileStore as RealStore


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "cli.db"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE orders (id INTEGER PRIMARY KEY, total REAL);"
                       "INSERT INTO orders VALUES (1, 10), (2, 32.5);")
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(niq_cli, "ProfileStore", lambda: RealStore(tmp_path / "p.json"))


def run(capsys, *argv):
    code = niq_cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_discover_and_types(capsys):
    code, out = run(capsys, "discover")
    assert code == 0 and out["data"]["drivers"]["sqlite"] is True
    code, out = run(capsys, "types")
    assert {t["id"] for t in out["data"]} >= {"mssql", "postgresql", "mysql", "sqlite"}


def test_parse_command(capsys):
    code, out = run(capsys, "parse", "postgresql://u:pw@h:6000/db")
    assert code == 0 and out["data"]["port"] == 6000 and out["data"]["has_password"]
    assert "pw" not in json.dumps(out)
    code, out = run(capsys, "parse", "anlamsiz")
    assert code == 1 and not out["success"]


def test_diagnose_and_sql_without_saving(capsys, db):
    code, out = run(capsys, "diagnose", "--input", db)
    assert code == 0 and out["data"]["ok"]
    code, out = run(capsys, "sql", "--input", db, "SELECT SUM(total) AS t FROM orders")
    assert code == 0 and out["results"] == [{"t": 42.5}]
    code, out = run(capsys, "sql", "--input", db, "DELETE FROM orders")
    assert code == 1 and "SELECT" in out["error"]
    code, out = run(capsys, "connections", "list")
    assert out["data"] == []  # hiçbir şey kaydedilmedi


def test_save_requires_approval_then_works(capsys, db):
    code, out = run(capsys, "connections", "save", "--input", db, "--name", "yerel")
    assert code == 3 and out["needs_approval"] and out["action"] == "save_connection" and out["rerun_with"] == "--yes"
    assert run(capsys, "connections", "list")[1]["data"] == []

    code, out = run(capsys, "connections", "save", "--input", db, "--name", "yerel", "--yes")
    assert code == 0 and out["data"]["name"] == "yerel"

    code, out = run(capsys, "tables", "--connection", "yerel")
    assert code == 0 and out["data"] == ["orders"]
    code, out = run(capsys, "schema", "--connection", "yerel", "--tables", "orders")
    assert out["data"]["orders"]["primary_key"] == ["id"]

    code, out = run(capsys, "connections", "delete", "yerel")
    assert code == 3 and out["action"] == "delete_connection"
    code, out = run(capsys, "connections", "delete", "yerel", "--yes")
    assert code == 0 and run(capsys, "connections", "list")[1]["data"] == []


def test_usage_errors(capsys):
    code, out = run(capsys, "tables")
    assert code == 2 and "--connection" in out["error"]
    code, out = run(capsys, "tables", "--connection", "yok")
    assert code == 1 and "bulunamadı" in out["error"]


def test_password_comes_from_env_not_argv(capsys, monkeypatch):
    captured = {}

    def fake_diagnose(profile, password, **kwargs):
        captured["password"] = password
        return {"ok": True, "steps": []}

    monkeypatch.setattr(niq_cli.diagnostics, "diagnose", fake_diagnose)
    monkeypatch.setenv("MY_PW", "s3cret")
    code, _ = run(capsys, "diagnose", "--type", "postgresql", "--host", "h", "--database", "d",
                  "--user", "u", "--password-env", "MY_PW")
    assert code == 0 and captured["password"] == "s3cret"
