"""API testleri: gerçek bir SQLite dosyası + sahte AI."""
import sqlite3

import pytest

from backend.routes import routes


@pytest.fixture()
def sample_db(tmp_path):
    path = tmp_path / "shop.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE Customers (CustomerID INTEGER PRIMARY KEY, CompanyName TEXT NOT NULL, Country TEXT);
        CREATE TABLE Orders (OrderID INTEGER PRIMARY KEY, CustomerID INTEGER REFERENCES Customers(CustomerID),
                             Freight REAL);
        INSERT INTO Customers VALUES (1, 'Alfreds', 'Germany'), (2, 'Berglunds', 'Sweden');
        INSERT INTO Orders VALUES (1, 1, 10.5), (2, 1, 20.0), (3, 2, 5.0);
    """)
    conn.commit()
    conn.close()
    return str(path)


class FakeAI:
    ai_available = True
    model = "fake"
    config = {"provider": "openrouter"}

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def generate_sql(self, question, schema, examples=None, previous_sql=None, previous_error=None, dialect=None,
                     context="", turns=None):
        self.calls.append((previous_sql, previous_error, dialect.id if dialect else None))
        return {"sql": self.answers.pop(0), "explanation": "açıklama"}

    def analyze_results(self, *args, **kwargs):
        return None


@pytest.fixture()
def wired(client, sample_db, monkeypatch):
    monkeypatch.setattr(routes.state, "connector", None)
    monkeypatch.setattr(routes.state, "_history", routes.HistoryStore())
    monkeypatch.setattr(routes.state, "_knowledge", routes.KnowledgeStore())
    monkeypatch.setattr(routes.state, "_profile_in_background", lambda profile: None)
    monkeypatch.setattr(routes.state, "profiles", routes.ProfileStore())
    response = client.post("/api/set_db", json={"type": "sqlite", "database": sample_db})
    assert response.status_code == 200, response.get_json()
    return client


def post_query(client, **overrides):
    body = {"question": "toplam nakliye", "tables": ["Orders", "Customers"]}
    body.update(overrides)
    return client.post("/api/query", json=body)


def test_health_reports_missing_ai_key(client):
    data = client.get("/api/health").get_json()["data"]
    assert data["status"] == "healthy"
    assert data["ai_service_available"] is False


def test_tables_and_columns_from_real_database(wired):
    assert wired.get("/api/tables").get_json()["data"] == ["Customers", "Orders"]
    columns = wired.post("/api/columns", json={"tables": ["Orders"]}).get_json()["data"]
    assert columns == {"Orders": ["OrderID", "CustomerID", "Freight"]}


def test_query_success_saves_history(wired, monkeypatch):
    monkeypatch.setattr(routes.state, "ai_service", FakeAI([
        "SELECT c.Country, SUM(o.Freight) AS total FROM Orders o JOIN Customers c ON c.CustomerID = o.CustomerID GROUP BY c.Country ORDER BY c.Country"]))
    response = post_query(wired)
    body = response.get_json()
    assert response.status_code == 200 and body["success"]
    assert body["results"] == [{"Country": "Germany", "total": 30.5}, {"Country": "Sweden", "total": 5.0}]
    assert body["explanation"] == "açıklama"
    saved = wired.get("/api/queries").get_json()["data"]
    assert len(saved) == 1 and saved[0]["is_successful"]


def test_dangerous_sql_never_reaches_database_and_is_repaired(wired, monkeypatch):
    ai = FakeAI(["DROP TABLE Orders", "SELECT COUNT(*) AS n FROM Orders"])
    monkeypatch.setattr(routes.state, "ai_service", ai)
    body = post_query(wired).get_json()
    assert body["success"] and body["attempts"] == 2
    assert body["results"] == [{"n": 3}]
    assert ai.calls[1][0] == "DROP TABLE Orders" and ai.calls[1][1]  # hata modele geri verildi
    assert ai.calls[0][2] == "sqlite"  # lehçe modele iletildi
    assert wired.get("/api/tables").get_json()["data"] == ["Customers", "Orders"]  # tablo yerinde


def test_database_error_is_fed_back_to_model(wired, monkeypatch):
    ai = FakeAI(["SELECT Nope FROM Orders", "SELECT OrderID FROM Orders ORDER BY OrderID"])
    monkeypatch.setattr(routes.state, "ai_service", ai)
    body = post_query(wired).get_json()
    assert body["success"] and body["attempts"] == 2
    assert "Nope" in ai.calls[1][1]


def test_gives_up_after_repair_attempts(wired, monkeypatch):
    monkeypatch.setattr(routes.state, "ai_service", FakeAI(["DELETE FROM Orders"] * 3))
    response = post_query(wired)
    assert response.status_code == 400 and not response.get_json()["success"]


def test_query_rejects_unknown_table(wired, monkeypatch):
    monkeypatch.setattr(routes.state, "ai_service", FakeAI([]))
    assert post_query(wired, tables=["Nope"]).status_code == 400


def test_query_without_ai_key_returns_503(wired, monkeypatch):
    from backend.services.ai_service import AIService
    monkeypatch.setattr(routes.state, "ai_service", AIService())
    response = post_query(wired)
    assert response.status_code == 503
    assert response.get_json()["error_code"] == "AI_UNAVAILABLE"


def test_suggest_tables_from_question(wired):
    suggested = wired.post("/api/suggest_tables", json={"question": "müşteri ve orders toplamı"}).get_json()["data"]
    assert "Orders" in suggested


def test_rejects_non_local_host_header(client):
    assert client.get("/api/health", headers={"Host": "evil.example.com"}).status_code == 403


def test_rejects_cross_origin_post(client):
    response = client.post("/api/settings", json={}, headers={"Origin": "https://evil.example.com"})
    assert response.status_code == 403


def test_settings_roundtrip_hides_key(client, monkeypatch):
    saved = {}
    monkeypatch.setattr("backend.config.config.Settings.set_api_key", staticmethod(lambda p, k: saved.update({p: k})))
    response = client.post("/api/settings", json={
        "provider": "openrouter", "models": {"openrouter": "anthropic/claude-haiku-5.5"},
        "api_key": "sk-secret", "max_output_tokens": 4096,
    })
    data = response.get_json()["data"]
    assert saved == {"openrouter": "sk-secret"}
    assert data["models"]["openrouter"] == "anthropic/claude-haiku-5.5"
    assert data["max_output_tokens"] == 4096
    assert "sk-secret" not in response.get_data(as_text=True)


def test_settings_validation(client):
    assert client.post("/api/settings", json={"provider": "nope"}).status_code == 400
    assert client.post("/api/settings", json={"local_base_url": "ftp://x"}).status_code == 400


def test_enabling_ai_analysis_needs_approval(client):
    response = client.post("/api/settings", json={"ai_analysis": True})
    assert response.status_code == 409
    assert response.get_json()["needs_approval"] and response.get_json()["action"] == "enable_ai_analysis"
    assert client.post("/api/settings", json={"ai_analysis": True, "approved": True}).status_code == 200
    assert client.post("/api/settings", json={"ai_analysis": False}).status_code == 200  # kapatmak onaysız
