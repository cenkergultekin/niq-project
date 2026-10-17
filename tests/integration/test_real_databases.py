"""
Gerçek veritabanı sunucularıyla entegrasyon testleri.

Ortam değişkeni verilmezse atlanır (normal CI ve geliştirme akışını yavaşlatmaz). Biçim: host:port:veritabanı:kullanıcı:parola
  NIQ_IT_POSTGRES=127.0.0.1:55432:niq_it:niq_admin:niqpass123
  NIQ_IT_MYSQL=127.0.0.1:53306:niq_it:root:niqpass123      (MariaDB de olur)
  NIQ_IT_MSSQL=127.0.0.1:1433:niq_it:sa:Niq_pass123!
Veritabanı (niq_it) önceden oluşturulmuş olmalı; testler içindeki tabloları kendileri kurup siler.
"""
import os
import secrets
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from test_network_security import ssh_env as ssh_env

from backend.config.config import Config
from backend.db import diagnostics
from backend.db.connector import Connector
from backend.db.dialects import build_url
from backend.db.profiles import Profile
from backend.services.query_service import run_sql

DDL = {
    "postgresql": [
        "DROP TABLE IF EXISTS it_orders", "DROP TABLE IF EXISTS it_customers",
        "CREATE TABLE it_customers (id INTEGER PRIMARY KEY, name VARCHAR(40) NOT NULL, country VARCHAR(20))",
        "CREATE TABLE it_orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES it_customers(id), total NUMERIC(10,2))",
    ],
    "mysql": [
        "DROP TABLE IF EXISTS it_orders", "DROP TABLE IF EXISTS it_customers",
        "CREATE TABLE it_customers (id INT PRIMARY KEY, name VARCHAR(40) NOT NULL, country VARCHAR(20))",
        "CREATE TABLE it_orders (id INT PRIMARY KEY, customer_id INT, total DECIMAL(10,2), "
        "FOREIGN KEY (customer_id) REFERENCES it_customers(id))",
    ],
    "mssql": [
        "IF OBJECT_ID('it_orders') IS NOT NULL DROP TABLE it_orders",
        "IF OBJECT_ID('it_customers') IS NOT NULL DROP TABLE it_customers",
        "CREATE TABLE it_customers (id INT PRIMARY KEY, name NVARCHAR(40) NOT NULL, country NVARCHAR(20))",
        "CREATE TABLE it_orders (id INT PRIMARY KEY, customer_id INT REFERENCES it_customers(id), total DECIMAL(10,2))",
    ],
}
DROP = {
    "postgresql": ["DROP TABLE IF EXISTS it_orders", "DROP TABLE IF EXISTS it_customers"],
    "mysql": ["DROP TABLE IF EXISTS it_orders", "DROP TABLE IF EXISTS it_customers"],
    "mssql": ["IF OBJECT_ID('it_orders') IS NOT NULL DROP TABLE it_orders",
              "IF OBJECT_ID('it_customers') IS NOT NULL DROP TABLE it_customers"],
}


def configured():
    found = []
    for db_type, env in (("postgresql", "NIQ_IT_POSTGRES"), ("mysql", "NIQ_IT_MYSQL"),
                         ("mysql", "NIQ_IT_MARIADB"), ("mssql", "NIQ_IT_MSSQL")):
        raw = os.environ.get(env)
        if raw:
            host, port, database, user, password = raw.split(":", 4)
            found.append(pytest.param((db_type, host, int(port), database, user, password), id=env.removeprefix("NIQ_IT_").lower()))
    return found or [pytest.param(None, id="yapilandirilmadi", marks=pytest.mark.skip(reason="NIQ_IT_* ortam değişkeni yok"))]


@pytest.fixture(params=configured())
def server(request):
    db_type, host, port, database, user, password = request.param
    profile = Profile(type=db_type, host=host, port=port, database=database, user=user)
    admin = create_engine(build_url(db_type, host, port, database, user, password, {"trust_server_cert": "yes"}))
    with admin.begin() as conn:
        for statement in DDL[db_type]:
            conn.execute(text(statement))
        conn.execute(text("INSERT INTO it_customers (id, name, country) VALUES (1, 'Ali', 'Germany'), (2, 'Veli', 'Sweden')"))
        conn.execute(text("INSERT INTO it_orders (id, customer_id, total) VALUES (1, 1, 10.50), (2, 1, 20.00), (3, 2, 5.25)"))
    connector = Connector(profile, password=password)
    yield connector
    connector.close()
    with admin.begin() as conn:
        for statement in DROP[db_type]:
            conn.execute(text(statement))
    admin.dispose()


def test_connection_tables_and_schema(server):
    assert server.test_connection()
    tables = server.get_tables(refresh=True)
    assert "it_customers" in tables and "it_orders" in tables
    info = server.get_table_info("it_orders")
    assert info.primary_key == ["id"]
    assert ("customer_id", "it_customers", "id") in info.foreign_keys
    assert [c.name for c in info.columns] == ["id", "customer_id", "total"]


def test_select_join_like_and_row_limit(server):
    result = server.execute_readonly(
        "SELECT c.name, SUM(o.total) AS t FROM it_orders o JOIN it_customers c ON c.id = o.customer_id "
        "GROUP BY c.name ORDER BY c.name", 10)
    assert result.is_successful, result.error
    assert [(r["name"], float(r["t"])) for r in result.results] == [("Ali", 30.5), ("Veli", 5.25)]
    like = server.execute_readonly("SELECT name FROM it_customers WHERE name LIKE 'A%i'", 10)
    assert like.is_successful and [r["name"] for r in like.results] == ["Ali"]  # % sürücü yer tutucusu sanılmadı
    limited = server.execute_readonly("SELECT id FROM it_orders ORDER BY id", 2)
    assert len(limited.results) == 2 and limited.truncated


def test_database_refuses_writes_even_without_the_guard(server):
    """SQL doğrulayıcı atlansa bile oturum salt-okunur olmalı (PostgreSQL/MySQL: veritabanı reddeder; her durumda rollback)."""
    attempt = server.execute_readonly("DELETE FROM it_orders", 10)
    after = server.execute_readonly("SELECT COUNT(*) AS n FROM it_orders", 10)
    assert after.results[0]["n"] == 3, "satırlar silindi"
    if server.profile.type in ("postgresql", "mysql"):
        assert not attempt.is_successful


def test_guard_and_run_sql_on_real_dialect(server):
    ok = run_sql(server, "SELECT COUNT(*) AS n FROM it_orders")
    assert ok["success"] and ok["results"][0]["n"] == 3
    assert not run_sql(server, "DROP TABLE it_orders")["success"]
    assert not run_sql(server, "SELECT * FROM it_orders; DELETE FROM it_orders")["success"]


def test_list_databases_and_diagnose(server):
    assert server.profile.database in server.list_databases()
    result = diagnostics.diagnose(server.profile, server._password)
    assert result["ok"], result
    assert [s["id"] for s in result["steps"]][:4] == ["driver", "network", "login", "read"]


@pytest.mark.parametrize("database", ["", "this_database_does_not_exist"])
def test_list_databases_before_database_name_is_known(server, database):
    browser = Connector(replace(server.profile, database=database), password=server._password)
    try:
        assert server.profile.database in browser.list_databases()
    finally:
        browser.close()


def test_http_connection_flow_and_cleanup(server, client, monkeypatch):
    """Gerçek sürücüyle yapıştır -> listele -> teşhis -> bağlan -> şema -> sorgu -> kapat."""
    from types import SimpleNamespace

    from backend.routes import routes

    monkeypatch.setattr(routes.state, "connector", None)
    monkeypatch.setattr(routes.state, "_profile_in_background", lambda *args: None)
    monkeypatch.setattr(routes.state, "ai_service", SimpleNamespace(
        ai_available=True, model="fixture", config={"provider": "local"},
        generate_sql=lambda *args, **kwargs: {"sql": "SELECT COUNT(*) AS n FROM it_orders", "explanation": "Sipariş sayısı"}))
    writes = []
    monkeypatch.setattr(routes, "set_password", lambda *args: writes.append(args))
    profile = server.profile
    uri = build_url(profile.type, profile.host, profile.port, profile.database, profile.user, server._password)
    # SQL Server build_url bir SQLAlchemy odbc_connect URL'sidir; kullanıcıya sunulan ODBC metnini kullan.
    if profile.type == "mssql":
        from urllib.parse import unquote_plus
        uri = unquote_plus(uri.split("odbc_connect=", 1)[1])
    parsed = client.post("/api/db/parse", json={"input": uri}).get_json()["data"]
    assert parsed["recognized"] and parsed["type"] == profile.type and parsed["has_password"]
    assert "password" not in parsed
    body = {"connection_input": uri}
    listed = client.post("/api/db/databases", json=body)
    assert listed.status_code == 200 and profile.database in listed.get_json()["data"]
    assert client.post("/api/db/diagnose", json=body).get_json()["data"]["ok"]
    connected = client.post("/api/set_db", json=body)
    assert connected.status_code == 200, connected.get_json()
    assert not writes, "kayıtsız bağlantı parolayı kalıcı depoya yazdı"
    assert "it_orders" in client.get("/api/tables").get_json()["data"]
    queried = client.post("/api/query", json={"question": "Kaç sipariş var?", "mode": "query"})
    assert queried.status_code == 200, queried.get_json()
    assert queried.get_json()["results"] == [{"n": 3}]
    denied = client.post("/api/connections/current", json={})
    assert denied.status_code == 409 and not writes
    assert client.post("/api/clear_db", json={}).status_code == 200
    assert not client.get("/api/health").get_json()["data"]["database_connected"]


def test_wrong_password_gives_actionable_hint(server):
    bad = diagnostics.diagnose(server.profile, "bu-parola-yanlis")
    assert not bad["ok"]
    login = next(s for s in bad["steps"] if s["id"] == "login")
    assert login["status"] == "fail" and login["hint"], login


def test_real_database_through_ssh_tunnel(server, ssh_env, tmp_path, monkeypatch):
    from backend.db.tunnel import KnownHosts

    monkeypatch.setattr("backend.db.tunnel.KnownHosts", lambda: KnownHosts(tmp_path / "known-hosts.json"))
    profile = replace(server.profile, options={"ssh_host": "127.0.0.1", "ssh_port": str(ssh_env["ssh_port"]),
                                               "ssh_user": "tunnel"})
    tunneled = Connector(profile, password=server._password, ssh_secret="secret", trust_ssh_host=True)
    try:
        assert tunneled.test_connection()
        assert "it_orders" in tunneled.get_tables()
        assert run_sql(tunneled, "SELECT COUNT(*) AS n FROM it_orders")["results"] == [{"n": 3}]
    finally:
        tunneled.close()
    assert tunneled._tunnel is None


def test_requested_tls_is_encrypted_or_explicitly_rejected(server):
    secured = Connector(replace(server.profile, options={"ssl": "require"}), password=server._password)
    checks = {"postgresql": "SELECT ssl AS encrypted FROM pg_stat_ssl WHERE pid = pg_backend_pid()",
              "mysql": "SHOW SESSION STATUS LIKE 'Ssl_cipher'",
              "mssql": "SELECT encrypt_option AS encrypted FROM sys.dm_exec_connections WHERE session_id = @@SPID"}
    try:
        result = secured.execute_readonly(checks[server.profile.type], 10)
        if result.is_successful:
            row = result.results[0]
            assert row.get("encrypted") in (True, "TRUE") or bool(row.get("Value")), row
        else:
            diagnosed = diagnostics.diagnose(secured.profile, server._password)
            assert not diagnosed["ok"]
            assert any(step["status"] == "fail" and step["hint"] for step in diagnosed["steps"])
    finally:
        secured.close()


def test_postgres_certificate_verification(server):
    if server.profile.type != "postgresql":
        return  # Diğer lehçelerin sertifikası bu PostgreSQL test CA'sıyla imzalı değildir.
    cert = Path(tempfile.gettempdir()) / "niq-local-postgres.crt"
    if not cert.exists():
        pytest.skip("Yerel TLS fixture'ı yok; tests/local_databases.py kullanın")
    verified = Connector(replace(server.profile, options={"ssl": "verify", "ssl_ca": str(cert)}), password=server._password)
    try:
        assert verified.test_connection()
        assert run_sql(verified, "SELECT COUNT(*) AS n FROM it_orders")["results"] == [{"n": 3}]
    finally:
        verified.close()


def test_actual_readonly_account_and_permission_diagnostics(server):
    """Salt-okunur kullanıcı gerçekten bağlanır; DB yazmayı guard'dan bağımsız reddeder."""
    name = "niq_reader_" + secrets.token_hex(5)
    password = "Niq_" + secrets.token_hex(16) + "!"
    kind = server.profile.type
    admin = create_engine(server._url(), isolation_level="AUTOCOMMIT")
    setup = {
        "postgresql": [f"CREATE ROLE {name} LOGIN PASSWORD '{password}'", f"GRANT USAGE ON SCHEMA public TO {name}",
                       f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {name}"],
        "mysql": [f"CREATE USER '{name}'@'%' IDENTIFIED BY '{password}'", f"GRANT SELECT ON niq_it.* TO '{name}'@'%'"],
        "mssql": [f"CREATE LOGIN [{name}] WITH PASSWORD='{password}', CHECK_POLICY=OFF",
                  f"CREATE USER [{name}] FOR LOGIN [{name}]", f"ALTER ROLE db_datareader ADD MEMBER [{name}]"],
    }
    cleanup = {
        "postgresql": [f"DROP OWNED BY {name}", f"DROP ROLE {name}"],
        "mysql": [f"DROP USER '{name}'@'%'"],
        "mssql": [f"DROP USER [{name}]", f"DROP LOGIN [{name}]"],
    }
    with admin.connect() as conn:
        for sql in setup[kind]:
            conn.execute(text(sql))
    readonly = Connector(replace(server.profile, user=name), password=password)
    try:
        result = diagnostics.diagnose(readonly.profile, password)
        assert result["ok"], result
        assert next(s for s in result["steps"] if s["id"] == "privileges")["status"] == "ok"
        assert run_sql(readonly, "SELECT COUNT(*) AS n FROM it_orders")["results"] == [{"n": 3}]
        assert not readonly.execute_readonly("DELETE FROM it_orders", 10).is_successful
    finally:
        readonly.close()
        with admin.connect() as conn:
            for sql in cleanup[kind]:
                conn.execute(text(sql))
        admin.dispose()


def test_agent_cli_connects_without_persisting_credentials(server, monkeypatch, capsys):
    import json

    import niq_cli

    profile = server.profile
    monkeypatch.setenv("NIQ_LOCAL_PASSWORD", server._password)
    connection = ["--type", profile.type, "--host", profile.host, "--port", str(profile.port),
                  "--database", profile.database, "--user", profile.user, "--password-env", "NIQ_LOCAL_PASSWORD"]
    assert niq_cli.main(["diagnose", *connection]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["ok"]
    assert niq_cli.main(["sql", *connection, "SELECT COUNT(*) AS n FROM it_orders"]) == 0
    assert json.loads(capsys.readouterr().out)["results"] == [{"n": 3}]


def test_query_timeout_is_enforced(server, monkeypatch):
    monkeypatch.setattr(Config, "QUERY_TIMEOUT", 1)
    # MySQL tek başına SLEEP zaman aşımında hata yerine 1 döndürür.
    sleeps = {"postgresql": "SELECT pg_sleep(5)", "mysql": "SELECT id FROM it_orders WHERE SLEEP(5) = 0",
              "mssql": "WAITFOR DELAY '00:00:05'"}
    sleeper = Connector(server.profile, password=server._password)
    result = sleeper.execute_readonly(sleeps[server.profile.type], 10)
    sleeper.close()
    assert not result.is_successful and "saniye" in result.error, result.error
