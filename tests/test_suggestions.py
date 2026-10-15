"""Hazır sorular ve takip önerileri."""
import sqlite3

import pytest

from backend.db.connector import Connector
from backend.db.profiles import Profile
from backend.routes import routes
from backend.services.ai_service import AIService, AIServiceError, parse_sql_answer
from backend.services.knowledge import KnowledgeStore
from backend.services.suggestions import build_suggestions, schema_questions


class SuggestAI:
    def __init__(self, questions=None, fail=False):
        self.ai_available = True
        self.questions = questions or ["Bu ay kaç sipariş var?", "En çok satan 5 ürün hangisi?"]
        self.fail = fail
        self.calls = 0
        self.catalog = ""

    def suggest_questions(self, catalog, count=6):
        self.calls += 1
        self.catalog = catalog
        if self.fail:
            raise AIServiceError("model yok")
        return self.questions


@pytest.fixture()
def connector(tmp_path):
    path = tmp_path / "s.db"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, total REAL);"
                       "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT);")
    conn.commit()
    conn.close()
    return Connector(Profile(type="sqlite", database=str(path)))


def test_schema_questions_work_without_ai(connector):
    questions = schema_questions(connector)
    assert "customers tablosunda kaç kayıt var?" in questions
    assert "orders tablosunda total toplamı ve ortalaması nedir?" in questions
    assert not any("customer_id" in q or " id " in q for q in questions)


def test_ai_suggestions_are_cached_and_use_glossary(connector, tmp_path):
    knowledge = KnowledgeStore(tmp_path / "k.db")
    knowledge.add_term(connector.db_key, "aktif müşteri", "son 90 günde sipariş veren")
    ai = SuggestAI()
    first = build_suggestions(connector, ai, knowledge)
    assert first == {"questions": ai.questions, "source": "ai"}
    assert "orders(id INTEGER" in ai.catalog and "aktif müşteri = son 90 günde sipariş veren" in ai.catalog
    build_suggestions(connector, ai, knowledge)
    assert ai.calls == 1  # ikinci istek önbellekten
    build_suggestions(connector, ai, knowledge, refresh=True)
    assert ai.calls == 2


def test_falls_back_to_schema_when_ai_fails_or_missing(connector, tmp_path):
    knowledge = KnowledgeStore(tmp_path / "k.db")
    failed = build_suggestions(connector, SuggestAI(fail=True), knowledge)
    assert failed["source"] == "schema" and failed["questions"]
    offline = AIService()
    offline.client = None
    assert build_suggestions(connector, offline, KnowledgeStore(tmp_path / "k2.db"))["source"] == "schema"


def test_schema_cache_is_replaced_when_ai_becomes_available(connector, tmp_path):
    knowledge = KnowledgeStore(tmp_path / "k.db")
    offline = AIService()
    offline.client = None
    assert build_suggestions(connector, offline, knowledge)["source"] == "schema"
    assert build_suggestions(connector, SuggestAI(), knowledge)["source"] == "ai"


def test_follow_ups_are_parsed_and_optional():
    parsed = parse_sql_answer('{"sql": "SELECT 1", "explanation": "x", "follow_ups": ["Ülkeye göre?", " ", "Geçen yıl?"]}')
    assert parsed["follow_ups"] == ["Ülkeye göre?", "Geçen yıl?"]
    assert "follow_ups" not in parse_sql_answer('{"sql": "SELECT 1", "explanation": "x"}')


def test_suggestions_endpoint_and_follow_ups_in_query(client, connector, tmp_path, monkeypatch):
    from backend.services.history import HistoryStore

    class AI(SuggestAI):
        config = {"provider": "openrouter"}
        model = "fake"

        def generate_sql(self, *a, **k):
            return {"sql": "SELECT COUNT(*) AS n FROM orders", "explanation": "sayar", "follow_ups": ["Müşteriye göre?"]}

        def pick_tables(self, *a, **k):
            return []

        def analyze_results(self, *a, **k):
            return None

    monkeypatch.setattr(routes.state, "connector", None)
    monkeypatch.setattr(routes.state, "ai_service", AI())
    monkeypatch.setattr(routes.state, "_history", HistoryStore(tmp_path / "h.db"))
    monkeypatch.setattr(routes.state, "_knowledge", KnowledgeStore(tmp_path / "k.db"))
    monkeypatch.setattr(routes.state, "_profile_in_background", lambda profile: None)
    assert client.post("/api/set_db", json={"type": "sqlite", "database": connector.profile.database}).status_code == 200
    data = client.get("/api/suggestions").get_json()["data"]
    assert data["source"] == "ai" and "Bu ay kaç sipariş var?" in data["questions"]
    body = client.post("/api/query", json={"question": "kaç sipariş var"}).get_json()
    assert body["success"] and body["follow_ups"] == ["Müşteriye göre?"]
