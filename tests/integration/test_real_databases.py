"""
Gerçek veritabanı sunucularıyla entegrasyon testleri.

Ortam değişkeni verilmezse atlanır (normal CI ve geliştirme akışını yavaşlatmaz). Biçim: host:port:veritabanı:kullanıcı:parola
  NIQ_IT_POSTGRES=127.0.0.1:55432:niq_it:niq_admin:niqpass123
  NIQ_IT_MYSQL=127.0.0.1:53306:niq_it:root:niqpass123      (MariaDB de olur)
  NIQ_IT_MSSQL=127.0.0.1:1433:niq_it:sa:Niq_pass123!
Veritabanı (niq_it) önceden oluşturulmuş olmalı; testler içindeki tabloları kendileri kurup siler.
"""
import os

import pytest
from sqlalchemy import create_engine, text

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
    for db_type, env in (("postgresql", "NIQ_IT_POSTGRES"), ("mysql", "NIQ_IT_MYSQL"), ("mssql", "NIQ_IT_MSSQL")):
        raw = os.environ.get(env)
        if raw:
            host, port, database, user, password = raw.split(":", 4)
            found.append(pytest.param((db_type, host, int(port), database, user, password), id=db_type))
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


def test_wrong_password_gives_actionable_hint(server):
    bad = diagnostics.diagnose(server.profile, "bu-parola-yanlis")
    assert not bad["ok"]
    login = next(s for s in bad["steps"] if s["id"] == "login")
    assert login["status"] == "fail" and login["hint"], login


def test_query_timeout_is_enforced(server, monkeypatch):
    monkeypatch.setattr(Config, "QUERY_TIMEOUT", 1)
    sleeps = {"postgresql": "SELECT pg_sleep(5)", "mysql": "SELECT SLEEP(5)", "mssql": "WAITFOR DELAY '00:00:05'"}
    sleeper = Connector(server.profile, password=server._password)
    result = sleeper.execute_readonly(sleeps[server.profile.type], 10)
    sleeper.close()
    assert not result.is_successful and "saniye" in result.error, result.error
