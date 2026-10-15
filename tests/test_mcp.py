"""MCP sunucusu: araçlar, güvenlik sınırları ve agent yüzeyi."""
import asyncio
import sqlite3

import pytest

import niq_mcp
from backend.db.connector import Connector
from backend.db.profiles import Profile, ProfileStore
from backend.services.agent_api import AgentAPI
from backend.services.history import HistoryStore
from backend.services.jobs import JobStore
from backend.services.knowledge import KnowledgeStore


class FakeAI:
    ai_available = True
    model = "fake"
    config = {"provider": "openrouter"}

    def generate_sql(self, question, schema, examples=None, previous_sql=None, previous_error=None, dialect=None,
                     context="", turns=None):
        self.last_context = context
        return {"sql": "SELECT COUNT(*) AS n FROM orders", "explanation": "sayar"}

    def pick_tables(self, *a, **k):
        return []

    def analyze_results(self, *a, **k):
        return None


@pytest.fixture()
def api(tmp_path):
    path = tmp_path / "shop.db"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT);")
    conn.executemany("INSERT INTO orders VALUES (?, ?)", [(i, "A" if i % 2 else "P") for i in range(120)])
    conn.commit()
    conn.close()
    profiles = ProfileStore(tmp_path / "p.json")
    profiles.save(Profile(type="sqlite", name="magaza", database=str(path)))
    return AgentAPI(profiles=profiles, history=HistoryStore(tmp_path / "h.db"), knowledge=KnowledgeStore(tmp_path / "k.db"),
                    jobs=JobStore(tmp_path / "j.db"), ai=FakeAI())


def call(server, name, **args):
    result = asyncio.run(server.call_tool(name, args))
    assert not result.is_error
    return result.structured_content.get("result", result.structured_content)


def test_tool_surface_has_no_credential_or_automation_tools(api):
    server = niq_mcp.build_server(api)
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"status", "list_connections", "list_tables", "describe_tables", "get_glossary", "ask", "run_sql",
                     "add_glossary_term", "list_reports", "recent_results"}
    assert not any(word in n for n in names for word in ("save", "connect_", "schedule", "notify", "delete", "password"))


def test_tool_schemas_are_derived_from_signatures(api):
    tools = {t.name: t for t in asyncio.run(niq_mcp.build_server(api).list_tools())}
    schema = tools["run_sql"].input_schema
    assert schema["required"] == ["connection", "sql"]
    assert set(tools["ask"].input_schema["properties"]) == {"connection", "question", "tables"}


def test_list_and_describe(api):
    server = niq_mcp.build_server(api)
    connections = call(server, "list_connections")
    assert connections == [{"name": "magaza", "type": "sqlite", "host": "", "database": connections[0]["database"], "user": ""}]
    assert "password" not in str(connections)
    assert call(server, "list_tables", connection="magaza")["tables"] == [{"name": "orders"}]
    described = call(server, "describe_tables", connection="magaza", tables=["orders"])
    assert described["tables"]["orders"]["primary_key"] == ["id"] and described["dialect"] == "SQLite"


def test_run_sql_is_guarded_and_trims_rows(api):
    server = niq_mcp.build_server(api)
    ok = call(server, "run_sql", connection="magaza", sql="SELECT id FROM orders ORDER BY id")
    assert ok["success"] and ok["row_count"] == 120 and ok["shown"] == 50 and ok["truncated"] is True
    for bad in ("DELETE FROM orders", "SELECT 1; DROP TABLE orders", "SELECT * FROM sqlite_master"):
        refused = call(server, "run_sql", connection="magaza", sql=bad)
        assert refused["success"] is False and refused["error"]
    still = call(server, "run_sql", connection="magaza", sql="SELECT COUNT(*) AS n FROM orders")
    assert still["rows"] == [{"n": 120}]


def test_unknown_connection_explains_how_to_add_one(api):
    server = niq_mcp.build_server(api)
    result = call(server, "list_tables", connection="yok")
    assert result["success"] is False and "magaza" in result["error"] and "kullanıcı" in result["error"]


def test_glossary_flows_into_ask_prompt(api):
    server = niq_mcp.build_server(api)
    added = call(server, "add_glossary_term", connection="magaza", term="aktif sipariş", definition="status = 'A'")
    assert added["success"]
    glossary = call(server, "get_glossary", connection="magaza")
    assert glossary["glossary"][0]["term"] == "aktif sipariş"
    answer = call(server, "ask", connection="magaza", question="kaç aktif sipariş var")
    assert answer["success"] and answer["rows"] == [{"n": 120}] and answer["sql"].startswith("SELECT")
    assert "aktif sipariş: status = 'A'" in api.ai.last_context  # tanım modele gitti


def test_reports_and_status(api):
    server = niq_mcp.build_server(api)
    assert call(server, "list_reports") == [] and call(server, "recent_results") == []
    status = call(server, "status")
    assert status["connections"] == 1 and "ai_ready" in status
    assert Connector(api.profiles.get("magaza")).test_connection()
