"""Zamanlama, uyarı koşulları, iş çalıştırma, bildirimler, dışa aktarma ve API."""
import io
import sqlite3
from datetime import datetime, timedelta

import pytest

from backend.db.profiles import Profile, ProfileStore
from backend.routes import routes
from backend.services import exporter, notify
from backend.services.jobs import JobStore, Scheduler, run_job
from backend.services.schedule import (
    describe_condition,
    describe_schedule,
    evaluate_condition,
    next_run,
    parse_schedule,
    validate_condition,
    validate_schedule,
)

MON_8 = datetime(2026, 10, 12, 8, 0, 0)  # pazartesi


# --- zamanlama ---------------------------------------------------------------------------
def test_next_run_variants():
    assert next_run({"kind": "daily", "time": "09:00"}, MON_8) == datetime(2026, 10, 12, 9, 0)
    assert next_run({"kind": "daily", "time": "07:00"}, MON_8) == datetime(2026, 10, 13, 7, 0)
    assert next_run({"kind": "weekly", "day": 0, "time": "09:00"}, MON_8) == datetime(2026, 10, 12, 9, 0)
    assert next_run({"kind": "weekly", "day": 0, "time": "07:00"}, MON_8) == datetime(2026, 10, 19, 7, 0)
    assert next_run({"kind": "weekly", "day": 4, "time": "10:00"}, MON_8) == datetime(2026, 10, 16, 10, 0)
    assert next_run({"kind": "monthly", "day": 1, "time": "09:00"}, MON_8) == datetime(2026, 11, 1, 9, 0)
    assert next_run({"kind": "monthly", "day": 1, "time": "09:00"}, datetime(2026, 12, 5, 8)) == datetime(2027, 1, 1, 9, 0)
    assert next_run({"kind": "interval", "minutes": 30}, MON_8) == MON_8 + timedelta(minutes=30)


@pytest.mark.parametrize("text, expected", [
    ("her pazartesi 09:00", {"kind": "weekly", "day": 0, "time": "09:00"}),
    ("her cuma saat 17:30", {"kind": "weekly", "day": 4, "time": "17:30"}),
    ("her gün 8:30", {"kind": "daily", "time": "08:30"}),
    ("her sabah", {"kind": "daily", "time": "09:00"}),
    ("her 30 dakikada", {"kind": "interval", "minutes": 30}),
    ("her 2 saatte bir", {"kind": "interval", "minutes": 120}),
    ("her saat", {"kind": "interval", "minutes": 60}),
    ("haftalık", {"kind": "weekly", "day": 0, "time": "09:00"}),
    ("ayın 15'i 10:00", {"kind": "monthly", "day": 15, "time": "10:00"}),
    ("aylık", {"kind": "monthly", "day": 1, "time": "09:00"}),
])
def test_parse_schedule(text, expected):
    assert parse_schedule(text) == expected


def test_parse_schedule_unknown_and_validation():
    assert parse_schedule("bir ara") is None
    with pytest.raises(ValueError):
        validate_schedule({"kind": "interval", "minutes": 1})
    with pytest.raises(ValueError):
        validate_schedule({"kind": "daily", "time": "25:00"})
    with pytest.raises(ValueError):
        validate_schedule({"kind": "monthly", "day": 31})
    assert describe_schedule({"kind": "weekly", "day": 0, "time": "09:00"}) == "her pazartesi 09:00"
    assert describe_schedule({"kind": "interval", "minutes": 120}) == "her 2 saatte bir"


def test_condition_evaluation():
    rows = [{"oran": 7.5, "ad": "x"}]
    assert evaluate_condition({"op": ">", "value": 5.0}, rows) == {"fired": True, "observed": 7.5}
    assert evaluate_condition({"op": "<=", "value": 5.0, "column": "oran"}, rows)["fired"] is False
    assert evaluate_condition({"op": "rows>0"}, rows)["fired"] is True
    assert evaluate_condition({"op": "rows==0"}, [])["fired"] is True
    assert evaluate_condition({"op": ">", "value": 1.0}, [])["fired"] is False
    assert evaluate_condition(None, rows)["fired"] is True
    with pytest.raises(ValueError):
        validate_condition({"op": "~"})
    with pytest.raises(ValueError):
        validate_condition({"op": ">"})
    assert describe_condition({"op": ">", "value": 5.0, "column": "oran"}) == "oran > 5 ise"


# --- dışa aktarma --------------------------------------------------------------------------
def test_csv_has_bom_semicolon_and_blocks_formula_injection():
    data = exporter.to_csv([{"ad": "=HYPERLINK(\"x\")", "tutar": 1.5}, {"ad": "Çiğdem", "tutar": 2}])
    assert data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    assert text.splitlines()[0] == "ad;tutar" and "'=HYPERLINK" in text and "Çiğdem" in text


def test_xlsx_roundtrip():
    from openpyxl import load_workbook
    data = exporter.to_xlsx([{"a": 1, "b": "x"}, {"a": 2, "b": "=1+1"}])
    sheet = load_workbook(io.BytesIO(data)).active
    assert [c.value for c in sheet[1]] == ["a", "b"] and sheet["B3"].value == "'=1+1"


# --- iş çalıştırma ----------------------------------------------------------------------------
@pytest.fixture()
def env(tmp_path, monkeypatch):
    path = tmp_path / "shop.db"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT);"
                       "INSERT INTO orders VALUES (1,'ok'),(2,'iade'),(3,'iade'),(4,'ok');")
    conn.commit()
    conn.close()
    profiles = ProfileStore(tmp_path / "p.json")
    profile = profiles.save(Profile(type="sqlite", name="yerel", database=str(path)))
    store = JobStore(tmp_path / "jobs.db")
    sent = []
    monkeypatch.setattr(notify, "notify", lambda channels, title, text, rows=None, payload=None:
                        sent.append((channels, title, text)) or {c: "ok" for c in channels})
    return {"profiles": profiles, "profile": profile, "store": store, "sent": sent, "path": str(path)}


def make_job(env, **kwargs):
    defaults = dict(name="İade oranı", kind="alert", profile_id=env["profile"].id, question="iade oranı",
                    sql="SELECT 100.0 * SUM(CASE WHEN status = 'iade' THEN 1 ELSE 0 END) / COUNT(*) AS oran FROM orders",
                    schedule={"kind": "daily", "time": "09:00"}, condition={"op": ">", "value": 40.0},
                    channels=["inbox", "telegram"])
    defaults.update(kwargs)
    return env["store"].get(env["store"].create(**defaults))


def test_alert_fires_notifies_and_respects_cooldown(env):
    job = make_job(env)
    now = datetime(2026, 10, 12, 9, 0)
    result = run_job(job, env["store"], env["profiles"], now)
    assert result["status"] == "alert" and result["fired"] and len(env["sent"]) == 1
    assert "gözlenen: 50.0" in env["sent"][0][2]

    job = env["store"].get(job["id"])
    again = run_job(job, env["store"], env["profiles"], now + timedelta(hours=1))  # bekleme süresi içinde
    assert again["status"] == "alert" and len(env["sent"]) == 1  # ikinci bildirim yok
    later = run_job(env["store"].get(job["id"]), env["store"], env["profiles"], now + timedelta(days=1, minutes=1))
    assert later["status"] == "alert" and len(env["sent"]) == 2
    assert len(env["store"].runs(job_id=job["id"])) == 3  # gelen kutusu her çalışmayı tutar


def test_alert_stays_quiet_when_condition_false(env):
    job = make_job(env, condition={"op": ">", "value": 90.0})
    result = run_job(job, env["store"], env["profiles"], datetime(2026, 10, 12, 9, 0))
    assert result["status"] == "quiet" and env["sent"] == []
    run = env["store"].runs(job_id=job["id"])[0]
    assert run["status"] == "quiet" and run["rows"] == [{"oran": 50.0}]


def test_report_notifies_every_time_and_failure_is_reported_once(env):
    job = make_job(env, name="Sipariş sayısı", kind="report", condition=None, sql="SELECT COUNT(*) AS n FROM orders")
    assert run_job(job, env["store"], env["profiles"], datetime(2026, 10, 12, 9, 0))["status"] == "ok"
    assert len(env["sent"]) == 1

    broken = make_job(env, name="Bozuk", kind="report", condition=None, sql="SELECT * FROM yok")
    first = run_job(broken, env["store"], env["profiles"], datetime(2026, 10, 12, 9, 0))
    assert first["status"] == "error" and len(env["sent"]) == 2
    again = run_job(env["store"].get(broken["id"]), env["store"], env["profiles"], datetime(2026, 10, 13, 9, 0))
    assert again["status"] == "error" and len(env["sent"]) == 2  # aynı hata tekrar gönderilmedi


def test_missing_profile_is_recorded_as_error(env):
    job = make_job(env, profile_id="silinmis")
    assert run_job(job, env["store"], env["profiles"])["status"] == "error"


def test_scheduler_runs_only_due_jobs_and_reschedules(env):
    due = make_job(env, name="vadesi gelen", kind="report", condition=None, sql="SELECT 1 AS x FROM orders",
                   schedule={"kind": "interval", "minutes": 60})
    far = make_job(env, name="vadesi gelmeyen", kind="report", condition=None, sql="SELECT 1 AS x FROM orders",
                   schedule={"kind": "daily", "time": "03:00"})
    disabled = make_job(env, name="kapalı", kind="report", condition=None, sql="SELECT 1 AS x FROM orders",
                        schedule={"kind": "interval", "minutes": 5})
    env["store"].set_enabled(disabled["id"], False)
    scheduler = Scheduler(env["store"], env["profiles"])
    future = datetime.now() + timedelta(minutes=61)
    outcomes = scheduler.tick(future.replace(microsecond=0))
    names = [o["job"] for o in outcomes]
    assert names == ["vadesi gelen"] and far["name"] not in names
    refreshed = env["store"].get(due["id"])
    assert datetime.fromisoformat(refreshed["next_run"]) > future  # bir sonraki çalışma planlandı
    assert scheduler.tick(future.replace(microsecond=0)) == []


def test_job_validation(env):
    with pytest.raises(ValueError):
        make_job(env, condition=None)  # uyarı koşulsuz olamaz
    with pytest.raises(ValueError):
        make_job(env, kind="weird")
    with pytest.raises(ValueError):
        make_job(env, schedule={"kind": "interval", "minutes": 2})


# --- bildirim kanalları -----------------------------------------------------------------------
def test_telegram_message_and_missing_config(monkeypatch):
    calls = []

    class Resp:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(notify.requests, "post", lambda url, **kw: calls.append((url, kw)) or Resp())
    monkeypatch.setattr(notify, "get_telegram_token", lambda: "123:ABC")
    from backend.config.config import Settings
    Settings.save({"notify": {"telegram_chat_id": "42"}})
    rows = [{"a": i} for i in range(15)]
    result = notify.notify(["telegram"], "Başlık", "Metin", rows)
    assert result == {"telegram": "ok"}
    assert calls[0][0] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert calls[0][1]["json"]["chat_id"] == "42" and "Başlık" in calls[0][1]["json"]["text"]
    assert calls[1][0].endswith("/sendDocument")  # 10'dan fazla satır CSV olarak da gider

    monkeypatch.setattr(notify, "get_telegram_token", lambda: None)
    assert "yapılandırılmamış" in notify.notify(["telegram"], "t", "x")["telegram"]


def test_notification_errors_do_not_leak_token(monkeypatch):
    def boom(url, **kw):
        raise RuntimeError(f"hata {url}")

    monkeypatch.setattr(notify.requests, "post", boom)
    monkeypatch.setattr(notify, "get_telegram_token", lambda: "SECRET-TOKEN")
    from backend.config.config import Settings
    Settings.save({"notify": {"telegram_chat_id": "42"}})
    message = notify.notify(["telegram"], "t", "x")["telegram"]
    assert "SECRET-TOKEN" not in message and "RuntimeError" in message


# --- API --------------------------------------------------------------------------------------
class PlanAI:
    ai_available = True
    model = "fake"
    config = {"provider": "openrouter"}

    def __init__(self, plan):
        self.plan = plan

    def plan_job(self, text, now_iso):
        return self.plan

    def generate_sql(self, *args, **kwargs):
        return {"sql": "SELECT COUNT(*) AS n FROM orders", "explanation": "sayar"}

    def pick_tables(self, *a, **k):
        return []

    def analyze_results(self, *a, **k):
        return None


@pytest.fixture()
def api(client, env, tmp_path, monkeypatch):
    from backend.services.history import HistoryStore
    from backend.services.knowledge import KnowledgeStore
    monkeypatch.setattr(routes.state, "connector", None)
    monkeypatch.setattr(routes.state, "profiles", env["profiles"])
    monkeypatch.setattr(routes.state, "_jobs", env["store"])
    monkeypatch.setattr(routes.state, "_history", HistoryStore(tmp_path / "h.db"))
    monkeypatch.setattr(routes.state, "_knowledge", KnowledgeStore(tmp_path / "k.db"))
    monkeypatch.setattr(routes.state, "_profile_in_background", lambda profile: None)
    assert client.post("/api/connections/yerel/connect").status_code == 200
    return client


def test_plan_preview_then_create_needs_approval(api, monkeypatch):
    plan = {"name": "Haftalık sipariş", "kind": "report", "question": "sipariş sayısı",
            "schedule": {"kind": "weekly", "minutes": None, "time": "09:00", "day": 0},
            "condition": None, "channels": ["inbox"]}
    monkeypatch.setattr(routes.state, "ai_service", PlanAI(plan))
    preview = api.post("/api/jobs/plan", json={"text": "her pazartesi 9'da sipariş sayısını gönder"}).get_json()["data"]
    assert preview["schedule_text"] == "her pazartesi 09:00" and preview["preview"] == [{"n": 4}]
    assert api.get("/api/jobs").get_json()["data"] == []  # plan henüz kaydetmez

    body = {k: preview[k] for k in ("name", "kind", "question", "sql", "schedule", "condition", "channels")}
    denied = api.post("/api/jobs", json=body)
    assert denied.status_code == 409 and denied.get_json()["action"] == "create_schedule"
    created = api.post("/api/jobs", json={**body, "approved": True})
    assert created.status_code == 200 and created.get_json()["data"]["schedule_text"] == "her pazartesi 09:00"

    job_id = created.get_json()["data"]["id"]
    ran = api.post(f"/api/jobs/{job_id}/run").get_json()["data"]
    assert ran["status"] == "ok"
    inbox = api.get("/api/inbox").get_json()["data"]
    assert inbox[0]["job_name"] == "Haftalık sipariş" and inbox[0]["rows"] == [{"n": 4}]
    assert api.delete(f"/api/jobs/{job_id}").status_code == 409
    assert api.delete(f"/api/jobs/{job_id}", json={"approved": True}).status_code == 200


def test_job_rejects_unsafe_sql_and_unsaved_profile(api, env):
    bad = api.post("/api/jobs", json={"name": "x", "kind": "report", "sql": "DELETE FROM orders",
                                      "schedule": {"kind": "daily", "time": "09:00"}, "approved": True})
    assert bad.status_code == 400
    env["profiles"].delete("yerel")  # profil silinince zamanlama reddedilir
    again = api.post("/api/jobs", json={"name": "x", "kind": "report", "sql": "SELECT 1 FROM orders",
                                        "schedule": {"kind": "daily", "time": "09:00"}, "approved": True})
    assert again.status_code == 400 and "kaydedin" in again.get_json()["error"]


def test_export_endpoint(api):
    csv_response = api.post("/api/export", json={"sql": "SELECT id, status FROM orders ORDER BY id", "format": "csv"})
    assert csv_response.status_code == 200 and "attachment" in csv_response.headers["Content-Disposition"]
    assert csv_response.data.decode("utf-8-sig").splitlines()[1] == "1;ok"
    xlsx = api.post("/api/export", json={"sql": "SELECT id FROM orders", "format": "xlsx"})
    assert xlsx.data[:2] == b"PK"  # xlsx bir zip dosyasıdır
    assert api.post("/api/export", json={"sql": "DROP TABLE orders", "format": "csv"}).status_code == 400
    assert api.post("/api/export", json={"sql": "SELECT 1 FROM orders", "format": "pdf"}).status_code == 400


def test_notify_config_needs_approval_and_hides_token(api, monkeypatch):
    saved = {}
    monkeypatch.setattr(notify, "set_telegram_token", lambda t: saved.update(token=t))
    monkeypatch.setattr(notify, "get_telegram_token", lambda: saved.get("token"))
    denied = api.post("/api/notify", json={"telegram_chat_id": "7", "telegram_token": "tok-SECRET123"})
    assert denied.status_code == 409 and denied.get_json()["action"] == "configure_notifications"
    assert saved == {}
    ok = api.post("/api/notify", json={"telegram_chat_id": "7", "telegram_token": "tok-SECRET123", "approved": True})
    body = ok.get_json()["data"]
    assert body["telegram_chat_id"] == "7" and body["telegram_has_token"] is True
    assert "SECRET123" not in ok.get_data(as_text=True)
    assert api.post("/api/notify", json={"webhook_url": "ftp://x", "approved": True}).status_code == 400
