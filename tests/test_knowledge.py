"""Bilgi katmanı: sözlük, notlar, veri profili, doğrulanmış örnekler ve SQL düzeltme."""
import sqlite3

import pytest

from backend.db.connector import Connector
from backend.db.profiles import Profile
from backend.models.models import SavedQuery
from backend.routes import routes
from backend.services.history import HistoryStore
from backend.services.knowledge import KnowledgeStore
from backend.services.profiler import categorical_values, profile_database


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(tmp_path / "k.db")


@pytest.fixture()
def shop(tmp_path):
    path = tmp_path / "shop.db"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT, country TEXT, email TEXT, note TEXT, amount REAL);")
    rows = [(i, "AP"[i % 2], ["Germany", "Sweden", "France"][i % 3], f"u{i}@x.com", f"not {i}", i * 1.5) for i in range(60)]
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return str(path)


def test_glossary_crud_and_upsert(store):
    first = store.add_term("db1", "aktif müşteri", "Son 90 günde sipariş veren müşteri", "o.OrderDate >= DATEADD(day,-90,GETDATE())")
    assert store.add_term("db1", "Aktif Müşteri", "güncel tanım") == first  # aynı terim güncellenir
    store.add_term("db2", "aktif müşteri", "başka veritabanı")
    terms = store.list_glossary("db1")
    assert len(terms) == 1 and terms[0]["definition"] == "güncel tanım"
    assert store.delete_term("db1", first) and store.list_glossary("db1") == []
    assert len(store.list_glossary("db2")) == 1  # bağlantılar ayrı
    with pytest.raises(ValueError):
        store.add_term("db1", " ", "tanım")


def test_manual_note_is_not_overwritten_by_auto(store):
    store.set_note("db", "orders", "Siparişler, iadeler dahil", auto=False)
    store.set_note("db", "orders", "otomatik taslak", auto=True)
    assert store.get_notes("db")["orders"] == {"note": "Siparişler, iadeler dahil", "auto": False}
    store.set_note("db", "orders", "")
    assert store.get_notes("db") == {}


def test_categorical_values_skip_pii_ids_and_high_cardinality():
    rows = [{"id": i, "status": "AP"[i % 2], "email": f"a{i}@x.com", "country": ["TR", "DE"][i % 2],
             "amount": float(i), "note": f"serbest metin {i}"} for i in range(50)]
    found = categorical_values(rows)
    assert found == {"status": ["A", "P"], "country": ["DE", "TR"]}


def test_profile_database_and_prompt_context(shop, store):
    connector = Connector(Profile(type="sqlite", database=shop))
    summary = profile_database(connector, store)
    assert summary["tables"] == 1 and summary["columns"] == 2
    key = connector.db_key
    store.add_term(key, "ciro", "iade hariç toplam tutar")
    store.set_note(key, "orders", "Tüm siparişler")

    without_values = store.prompt_context(key, ["orders"], "ciro nedir", include_values=False)
    assert "ciro: iade hariç" in without_values and "orders: Tüm siparişler" in without_values
    assert "Germany" not in without_values  # izin yokken değerler gitmez

    with_values = store.prompt_context(key, ["orders"], "ciro nedir", include_values=True)
    assert "orders.country values: 'France', 'Germany', 'Sweden'" in with_values
    assert "email" not in with_values
    connector.close()


def test_verified_examples_rank_first(tmp_path):
    history = HistoryStore(tmp_path / "h.db")
    key = "db"
    plain = history.save(key, SavedQuery(question="toplam satış ülke", sql_query="SELECT 1", tables_used=["o"]))
    verified = history.save(key, SavedQuery(question="ülkeye göre toplam satış", sql_query="SELECT 2", tables_used=["o"]))
    history.save(key, SavedQuery(question="tamamen alakasız kelimeler", sql_query="SELECT 3", tables_used=["o"]))
    assert history.set_verified(key, verified, True)
    found = history.examples(key, "satış toplamı ülke bazında")
    assert [q.id for q in found][0] == verified and plain in [q.id for q in found]
    assert all(q.sql_query != "SELECT 3" for q in found)
    assert history.get(key, verified).verified is True


@pytest.fixture()
def api(client, shop, tmp_path, monkeypatch):
    monkeypatch.setattr(routes.state, "connector", None)
    monkeypatch.setattr(routes.state, "_history", HistoryStore(tmp_path / "h.db"))
    monkeypatch.setattr(routes.state, "_knowledge", KnowledgeStore(tmp_path / "k.db"))
    monkeypatch.setattr(routes.state, "_profile_in_background", lambda profile: None)
    assert client.post("/api/set_db", json={"type": "sqlite", "database": shop}).status_code == 200
    return client


def test_glossary_api(api):
    assert api.post("/api/knowledge/glossary", json={"term": "iptal", "definition": "status = 'C'"}).status_code == 200
    terms = api.get("/api/knowledge/glossary").get_json()["data"]
    assert terms[0]["term"] == "iptal"
    assert api.post("/api/knowledge/glossary", json={"term": "", "definition": "x"}).status_code == 400
    assert api.delete(f"/api/knowledge/glossary/{terms[0]['id']}").status_code == 200
    assert api.delete("/api/knowledge/glossary/999").status_code == 404


def test_notes_and_profile_api(api):
    assert api.post("/api/knowledge/notes", json={"table": "orders", "note": "Tüm siparişler"}).status_code == 200
    assert api.post("/api/knowledge/notes", json={"table": "yok", "note": "x"}).status_code == 400
    assert api.get("/api/knowledge/notes").get_json()["data"]["orders"]["note"] == "Tüm siparişler"
    assert api.post("/api/knowledge/profile").get_json()["data"]["tables"] == 1


def test_edit_query_runs_through_guard_and_verifies(api):
    qid = routes.state.history.save(routes.state.connector.db_key, SavedQuery(
        question="kaç sipariş", sql_query="SELECT 1", tables_used=["orders"], is_successful=False))
    bad = api.put(f"/api/queries/{qid}", json={"sql": "DELETE FROM orders"})
    assert bad.status_code == 400
    good = api.put(f"/api/queries/{qid}", json={"sql": "SELECT COUNT(*) AS n FROM orders"})
    assert good.status_code == 200 and good.get_json()["results"] == [{"n": 60}]
    saved = routes.state.history.get(routes.state.connector.db_key, qid)
    assert saved.verified and saved.is_successful and saved.sql_query == "SELECT COUNT(*) AS n FROM orders"
    assert api.put("/api/queries/999", json={"sql": "SELECT 1"}).status_code == 404


def test_verify_endpoint(api):
    qid = routes.state.history.save(routes.state.connector.db_key, SavedQuery(question="q", sql_query="SELECT 1"))
    assert api.post(f"/api/queries/{qid}/verify", json={"verified": True}).status_code == 200
    assert routes.state.history.get(routes.state.connector.db_key, qid).verified
    assert api.post("/api/queries/999/verify", json={}).status_code == 404


def test_enabling_value_samples_needs_approval(client):
    denied = client.post("/api/settings", json={"ai_value_samples": True})
    assert denied.status_code == 409 and denied.get_json()["action"] == "enable_value_samples"
    assert client.post("/api/settings", json={"ai_value_samples": True, "approved": True}).status_code == 200
    assert client.post("/api/settings", json={"ai_value_samples": False}).status_code == 200
