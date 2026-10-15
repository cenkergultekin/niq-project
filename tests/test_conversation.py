"""Otomatik tablo seçimi ve konuşmalı sorgu."""
import sqlite3

import pytest

from backend.routes import routes


class RecordingAI:
    ai_available = True
    model = "fake"
    config = {"provider": "openrouter"}

    def __init__(self, answers, picks=None):
        self.answers = list(answers)
        self.picks = picks or []
        self.calls = []
        self.pick_calls = 0

    def generate_sql(self, question, schema, examples=None, previous_sql=None, previous_error=None, dialect=None,
                     context="", turns=None):
        self.calls.append({"question": question, "tables": sorted(schema.tables), "turns": list(turns or []),
                           "error": previous_error})
        return {"sql": self.answers.pop(0), "explanation": "açıklama"}

    def pick_tables(self, question, catalog, limit=6):
        self.pick_calls += 1
        return self.picks

    def analyze_results(self, *args, **kwargs):
        return None


@pytest.fixture()
def big_db(tmp_path):
    """16 tablo: SMALL_SCHEMA sınırını aşar, otomatik seçim devreye girer."""
    path = tmp_path / "big.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, country TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), total REAL);
        INSERT INTO customers VALUES (1, 'A', 'Germany'), (2, 'B', 'Sweden');
        INSERT INTO orders VALUES (1, 1, 10), (2, 1, 20), (3, 2, 5);
    """)
    for i in range(14):
        conn.execute(f"CREATE TABLE misc_{i} (id INTEGER PRIMARY KEY, v TEXT)")
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture()
def api(client, big_db, tmp_path, monkeypatch):
    from backend.services.history import HistoryStore
    from backend.services.knowledge import KnowledgeStore
    monkeypatch.setattr(routes.state, "connector", None)
    monkeypatch.setattr(routes.state, "_history", HistoryStore(tmp_path / "h.db"))
    monkeypatch.setattr(routes.state, "_knowledge", KnowledgeStore(tmp_path / "k.db"))
    monkeypatch.setattr(routes.state, "_profile_in_background", lambda profile: None)
    assert client.post("/api/set_db", json={"type": "sqlite", "database": big_db}).status_code == 200
    return client


def ask(api, question, **extra):
    return api.post("/api/query", json={"question": question, **extra})


def test_tables_resolved_from_names_with_fk_neighbours(api, monkeypatch):
    ai = RecordingAI(["SELECT SUM(total) AS t FROM orders"])
    monkeypatch.setattr(routes.state, "ai_service", ai)
    body = ask(api, "orders toplamı nedir").get_json()
    assert body["success"] and body["auto_tables"] is True
    assert ai.calls[0]["tables"] == ["customers", "orders"]  # orders + FK komşusu customers
    assert "misc_0" not in ai.calls[0]["tables"] and ai.pick_calls == 0
    assert body["tables_used"] == ["orders"]


def test_model_picks_tables_when_names_do_not_match(api, monkeypatch):
    ai = RecordingAI(["SELECT COUNT(*) AS n FROM customers"], picks=["customers"])
    monkeypatch.setattr(routes.state, "ai_service", ai)
    body = ask(api, "kaç müşterimiz var").get_json()
    assert body["success"] and ai.pick_calls == 1
    assert ai.calls[0]["tables"] == ["customers"]


def test_unselected_known_table_is_added_then_retried(api, monkeypatch):
    ai = RecordingAI(["SELECT * FROM misc_3", "SELECT COUNT(*) AS n FROM misc_3"], picks=["customers"])
    monkeypatch.setattr(routes.state, "ai_service", ai)
    body = ask(api, "kaç müşterimiz var").get_json()
    assert body["success"] and body["attempts"] == 2
    assert "misc_3" in ai.calls[1]["tables"]
    assert "definitions were not provided" in ai.calls[1]["error"]


def test_follow_up_receives_previous_turns_and_tables(api, monkeypatch):
    ai = RecordingAI(["SELECT country, COUNT(*) AS n FROM customers GROUP BY country",
                      "SELECT COUNT(*) AS n FROM customers WHERE country = 'Germany'"], picks=["customers"])
    monkeypatch.setattr(routes.state, "ai_service", ai)
    first = ask(api, "müşterileri ülkeye göre say", conversation_id="c1").get_json()
    assert first["success"] and first["conversation_id"] == "c1"
    second = ask(api, "sadece Almanya", conversation_id="c1").get_json()
    assert second["success"]
    turns = ai.calls[1]["turns"]
    assert len(turns) == 1 and turns[0].question == "müşterileri ülkeye göre say"
    assert turns[0].columns == ["country", "n"]
    assert ai.calls[1]["tables"] == ["customers"]  # önceki turun tabloları taşındı
    assert ai.pick_calls == 1  # ikinci soruda yeniden seçim gerekmedi


def test_reset_conversation_forgets_context(api, monkeypatch):
    ai = RecordingAI(["SELECT COUNT(*) AS n FROM customers", "SELECT COUNT(*) AS n FROM customers"], picks=["customers"])
    monkeypatch.setattr(routes.state, "ai_service", ai)
    ask(api, "kaç müşteri var", conversation_id="c2")
    assert api.delete("/api/conversations/c2").status_code == 200
    ask(api, "kaç müşteri var", conversation_id="c2")
    assert ai.calls[1]["turns"] == []


def test_explicit_tables_still_work(api, monkeypatch):
    ai = RecordingAI(["SELECT COUNT(*) AS n FROM orders"])
    monkeypatch.setattr(routes.state, "ai_service", ai)
    body = ask(api, "say", tables=["orders"]).get_json()
    assert body["success"] and body["auto_tables"] is False and ai.calls[0]["tables"] == ["orders"]
