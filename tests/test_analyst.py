"""Çok adımlı analiz: sahte araç çağıran model, gerçek SQLite."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from backend.routes import routes
from backend.services.ai_service import AIServiceError
from backend.services.analyst import is_analytical


def call(name, **args):
    return SimpleNamespace(id=f"call_{name}_{len(args)}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def reply(*calls, content=""):
    return SimpleNamespace(content=content, tool_calls=list(calls))


class ScriptedAI:
    ai_available = True
    model = "fake"
    config = {"provider": "openrouter"}

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def chat(self, messages, tools, max_tokens=None):
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0)

    # tek-atımlık yol (geri dönüş testi)
    def generate_sql(self, *args, **kwargs):
        return {"sql": "SELECT COUNT(*) AS n FROM sales", "explanation": "tek sorgu"}

    def pick_tables(self, *args, **kwargs):
        return []

    def analyze_results(self, *args, **kwargs):
        return None


@pytest.fixture()
def api(client, tmp_path, monkeypatch):
    from backend.services.history import HistoryStore
    from backend.services.knowledge import KnowledgeStore
    path = tmp_path / "sales.db"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE sales (id INTEGER PRIMARY KEY, region TEXT, month TEXT, amount REAL);")
    rows = [(1, "DE", "2026-08", 100), (2, "DE", "2026-09", 40), (3, "SE", "2026-08", 50), (4, "SE", "2026-09", 55)]
    conn.executemany("INSERT INTO sales VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()
    monkeypatch.setattr(routes.state, "connector", None)
    monkeypatch.setattr(routes.state, "_history", HistoryStore(tmp_path / "h.db"))
    monkeypatch.setattr(routes.state, "_knowledge", KnowledgeStore(tmp_path / "k.db"))
    monkeypatch.setattr(routes.state, "_profile_in_background", lambda profile: None)
    assert client.post("/api/set_db", json={"type": "sqlite", "database": str(path)}).status_code == 200
    return client


def test_intent_detection():
    assert is_analytical("satışlar neden düştü?")
    assert is_analytical("bu ayı geçen ayla karşılaştır")
    assert is_analytical("why did revenue drop")
    assert not is_analytical("kaç sipariş var")
    assert not is_analytical("toplam ciro")


def test_multi_step_analysis_returns_steps_and_answer(api, monkeypatch):
    ai = ScriptedAI([
        reply(call("run_sql", purpose="aylık toplam", sql="SELECT month, SUM(amount) AS t FROM sales GROUP BY month ORDER BY month")),
        reply(call("run_sql", purpose="bölgeye göre", sql="SELECT region, month, amount FROM sales ORDER BY region, month")),
        reply(call("finish", title="Eylül düşüşü", answer="Toplam 150'den 95'e düştü; düşüşün tamamı DE'den.",
                   findings=["DE: 100 -> 40", "SE: 50 -> 55"])),
    ])
    monkeypatch.setattr(routes.state, "ai_service", ai)
    body = api.post("/api/query", json={"question": "satışlar eylülde neden düştü", "approved": True}).get_json()
    assert body["success"] and body["mode"] == "analysis"
    assert body["title"] == "Eylül düşüşü" and "DE" in body["answer"]
    assert [s["purpose"] for s in body["steps"]] == ["aylık toplam", "bölgeye göre"]
    assert body["steps"][0]["rows"] == [{"month": "2026-08", "t": 150.0}, {"month": "2026-09", "t": 95.0}]
    assert body["results"][0]["region"] == "DE"  # son başarılı sorgu tablo olarak döner
    last_tool = [m for m in ai.seen[-1] if m["role"] == "tool"][-1]["content"]
    assert "DE" in last_tool  # araç sonucu modele geri verildi
    saved = api.get("/api/queries").get_json()["data"][0]
    assert saved["question"].startswith("satışlar") and saved["is_successful"]


def test_analysis_needs_approval_once(api, monkeypatch):
    ai = ScriptedAI([reply(call("finish", title="t", answer="a", findings=[]))] * 2)
    monkeypatch.setattr(routes.state, "ai_service", ai)
    denied = api.post("/api/query", json={"question": "satışları analiz et"})
    assert denied.status_code == 409 and denied.get_json()["action"] == "enable_ai_analysis"
    assert api.post("/api/query", json={"question": "satışları analiz et", "approved": True}).status_code == 200
    assert api.post("/api/query", json={"question": "satışları analiz et"}).status_code == 200  # izin kalıcı


def test_unsafe_sql_inside_analysis_is_rejected_and_fed_back(api, monkeypatch):
    ai = ScriptedAI([
        reply(call("run_sql", purpose="kötü", sql="DELETE FROM sales")),
        reply(call("run_sql", purpose="iyi", sql="SELECT COUNT(*) AS n FROM sales")),
        reply(call("finish", title="t", answer="4 satır", findings=[])),
    ])
    monkeypatch.setattr(routes.state, "ai_service", ai)
    body = api.post("/api/query", json={"question": "tabloyu incele", "approved": True}).get_json()
    assert body["success"]
    assert body["steps"][0]["ok"] is False and "SELECT" in body["steps"][0]["error"]
    assert body["steps"][1]["ok"] is True
    check = routes.state.connector.execute_readonly("SELECT COUNT(*) AS n FROM sales", 5)
    assert check.results == [{"n": 4}]  # tablo yerinde


def test_query_budget_is_enforced(api, monkeypatch):
    many = [reply(call("run_sql", purpose=f"s{i}", sql="SELECT 1 AS x FROM sales")) for i in range(8)]
    ai = ScriptedAI(many + [reply(call("finish", title="t", answer="bitti", findings=[]))])
    monkeypatch.setattr(routes.state, "ai_service", ai)
    body = api.post("/api/query", json={"question": "derin analiz", "mode": "analyze", "approved": True}).get_json()
    assert body["success"] and len([s for s in body["steps"] if s["ok"]]) == 6  # en fazla MAX_STEPS sorgu çalışır


def test_plain_text_answer_counts_as_final(api, monkeypatch):
    ai = ScriptedAI([reply(content="Veri yetersiz, ama toplam 4 kayıt var.")])
    monkeypatch.setattr(routes.state, "ai_service", ai)
    body = api.post("/api/query", json={"question": "durumu incele", "approved": True}).get_json()
    assert body["success"] and "4 kayıt" in body["answer"]


def test_auto_mode_falls_back_to_single_query_on_tool_failure(api, monkeypatch):
    class NoTools(ScriptedAI):
        def chat(self, messages, tools, max_tokens=None):
            raise AIServiceError("model araç çağırmayı desteklemiyor")

    monkeypatch.setattr(routes.state, "ai_service", NoTools([]))
    body = api.post("/api/query", json={"question": "satışları incele", "approved": True}).get_json()
    assert body["success"] and body.get("mode") != "analysis" and body["explanation"] == "tek sorgu"
    forced = api.post("/api/query", json={"question": "satışları incele", "mode": "analyze", "approved": True})
    assert forced.status_code == 502  # zorunlu analiz modunda hata gizlenmez
