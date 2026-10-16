"""
Zamanlanmış raporlar ve uyarılar.

Bir iş, doğrulanmış bir SQL'i (AI her çalıştırmada tekrar çağrılmaz: hızlı, ucuz, deterministik) bir zamanlamayla
çalıştırır. Rapor işleri her seferinde, uyarı işleri yalnızca koşul sağlanınca (ve bekleme süresi geçmişse)
bildirim gönderir. Sonuçlar her zaman uygulama içi gelen kutusuna yazılır.

İşler yalnızca uygulama açıkken kendiliğinden çalışır; kapalıyken kaçan işler açılışta bir kez çalıştırılır.
Uygulama kapalıyken de çalışması için `niq_cli.py jobs run-due` Windows Görev Zamanlayıcı'dan çağrılabilir.
"""
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from backend.config.config import data_dir
from backend.db.connector import Connector
from backend.db.profiles import ProfileStore
from backend.services import notify as notifier
from backend.services.query_service import run_sql
from backend.services.schedule import (
    describe_condition,
    describe_schedule,
    evaluate_condition,
    next_run,
    validate_condition,
    validate_schedule,
)

logger = logging.getLogger(__name__)

RUN_ROWS_KEPT = 50
ALERT_COOLDOWN_MINUTES = 24 * 60


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


class JobStore:
    def __init__(self, path: Optional[str] = None):
        self.path = str(path or data_dir() / "jobs.db")
        self._lock = threading.Lock()
        with self._lock, self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT NOT NULL,
                    profile_id TEXT NOT NULL, question TEXT, sql TEXT NOT NULL, schedule TEXT NOT NULL,
                    condition TEXT, channels TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                    cooldown_minutes INTEGER NOT NULL DEFAULT 1440, next_run TEXT NOT NULL,
                    last_run TEXT, last_status TEXT, last_message TEXT, last_alert_at TEXT, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL, job_name TEXT NOT NULL,
                    ran_at TEXT NOT NULL, status TEXT NOT NULL, message TEXT, row_count INTEGER,
                    fired INTEGER NOT NULL DEFAULT 0, result_json TEXT, channels_json TEXT, sql TEXT);
                CREATE INDEX IF NOT EXISTS idx_runs_job ON runs(job_id, id DESC);
            """)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _job(row: sqlite3.Row) -> Dict[str, Any]:
        job = dict(row)
        job["schedule"] = json.loads(job["schedule"])
        job["condition"] = json.loads(job["condition"]) if job["condition"] else None
        job["channels"] = json.loads(job["channels"])
        job["enabled"] = bool(job["enabled"])
        job["schedule_text"] = describe_schedule(job["schedule"])
        job["condition_text"] = describe_condition(job["condition"])
        return job

    def create(self, name: str, kind: str, profile_id: str, question: str, sql: str, schedule: Dict[str, Any],
               condition: Optional[Dict[str, Any]] = None, channels: Optional[List[str]] = None,
               cooldown_minutes: int = ALERT_COOLDOWN_MINUTES) -> int:
        if kind not in ("report", "alert"):
            raise ValueError("Tür 'report' veya 'alert' olmalı")
        schedule = validate_schedule(schedule)
        condition = validate_condition(condition)
        if kind == "alert" and not condition:
            raise ValueError("Uyarı için koşul gerekli")
        channels = [c for c in (channels or ["inbox"]) if c in notifier.CHANNELS] or ["inbox"]
        if not name.strip() or not sql.strip():
            raise ValueError("Ad ve SQL gerekli")
        first = next_run(schedule, _now())
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO jobs (name, kind, profile_id, question, sql, schedule, condition, channels,
                       cooldown_minutes, next_run, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (name.strip(), kind, profile_id, question, sql.strip(), json.dumps(schedule),
                 json.dumps(condition) if condition else None, json.dumps(channels), int(cooldown_minutes),
                 first.isoformat(), _now().isoformat()))
            return int(cur.lastrowid)

    def list(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            return [self._job(r) for r in conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()]

    def get(self, job_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def delete(self, job_id: int) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM runs WHERE job_id = ?", (job_id,))
            return conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,)).rowcount > 0

    def set_enabled(self, job_id: int, enabled: bool) -> bool:
        with self._lock, self._connect() as conn:
            if enabled:
                job = self.get(job_id)
                if job:
                    conn.execute("UPDATE jobs SET next_run = ? WHERE id = ?",
                                 (next_run(job["schedule"], _now()).isoformat(), job_id))
            return conn.execute("UPDATE jobs SET enabled = ? WHERE id = ?", (int(enabled), job_id)).rowcount > 0

    def due(self, now: datetime) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM jobs WHERE enabled = 1 AND next_run <= ? ORDER BY next_run",
                                (now.isoformat(),)).fetchall()
        return [self._job(r) for r in rows]

    def record_run(self, job: Dict[str, Any], status: str, message: str, row_count: Optional[int], fired: bool,
                   rows: Optional[List[Dict[str, Any]]], channel_results: Dict[str, str], now: datetime,
                   alerted: bool) -> int:
        payload = json.dumps((rows or [])[:RUN_ROWS_KEPT], ensure_ascii=False, default=str) if rows is not None else None
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO runs (job_id, job_name, ran_at, status, message, row_count, fired, result_json,
                       channels_json, sql) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (job["id"], job["name"], now.isoformat(), status, message, row_count, int(fired), payload,
                 json.dumps(channel_results, ensure_ascii=False), job["sql"]))
            conn.execute(
                """UPDATE jobs SET last_run = ?, last_status = ?, last_message = ?, next_run = ?,
                       last_alert_at = CASE WHEN ? THEN ? ELSE last_alert_at END WHERE id = ?""",
                (now.isoformat(), status, message[:300], next_run(job["schedule"], now).isoformat(),
                 int(alerted), now.isoformat(), job["id"]))
            return int(cur.lastrowid)

    def runs(self, limit: int = 30, job_id: Optional[int] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM runs " + ("WHERE job_id = ? " if job_id else "") + "ORDER BY id DESC LIMIT ?"
        args = (job_id, limit) if job_id else (limit,)
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["rows"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
            item["channels"] = json.loads(item.pop("channels_json")) if item.get("channels_json") else {}
            item["fired"] = bool(item["fired"])
            result.append(item)
        return result

    def get_run(self, run_id: int) -> Optional[Dict[str, Any]]:
        for run in self.runs(limit=500):
            if run["id"] == run_id:
                return run
        return None


def run_job(job: Dict[str, Any], store: JobStore, profiles: ProfileStore, now: Optional[datetime] = None) -> Dict[str, Any]:
    """İşi çalıştırır, koşulu değerlendirir, bildirimleri gönderir, sonucu kaydeder."""
    now = now or _now()
    profile = profiles.get(job["profile_id"])
    previous_status = job.get("last_status")
    if not profile:
        message = "Bağlantı profili bulunamadı; profil silinmiş olabilir."
        store.record_run(job, "error", message, None, False, None, {}, now, False)
        return {"status": "error", "message": message}

    connector = Connector(profile)
    try:
        result = run_sql(connector, job["sql"])
    except Exception as exc:  # tünel/bağlantı hatası
        result = {"success": False, "error": str(exc)}
    finally:
        connector.close()

    external = [c for c in job["channels"] if c != "inbox"]
    if not result.get("success"):
        message = f"Çalışmadı: {result.get('error')}"
        sent: Dict[str, str] = {}
        if external and previous_status != "error":  # aynı hatayı her çalışmada tekrar göndermez
            sent = notifier.notify(external, f"NIQ: {job['name']} çalışmadı", str(result.get("error")))
        store.record_run(job, "error", message, None, False, None, sent, now, False)
        return {"status": "error", "message": message}

    rows = result["results"]
    check = evaluate_condition(job["condition"], rows)
    fired = check["fired"]
    sent = {}
    alerted = False
    if job["kind"] == "report":
        status, message = "ok", f"{len(rows)} satır"
        if external:
            sent = notifier.notify(external, f"NIQ raporu: {job['name']}", f"{len(rows)} satır ({now:%d.%m.%Y %H:%M})", rows)
    else:
        last_alert = job.get("last_alert_at")
        cooling = bool(last_alert) and now < datetime.fromisoformat(last_alert) + timedelta(minutes=job["cooldown_minutes"])
        if fired:
            status = "alert"
            message = f"Uyarı: {job['condition_text']} (gözlenen: {check['observed']})"
            if external and not cooling:
                sent = notifier.notify(external, f"NIQ uyarısı: {job['name']}", message, rows)
                alerted = True
            elif not cooling:
                alerted = True
        else:
            status, message = "quiet", f"Koşul sağlanmadı (gözlenen: {check['observed']})"
    store.record_run(job, status, message, len(rows), fired, rows, sent, now, alerted)
    return {"status": status, "message": message, "row_count": len(rows), "fired": fired, "channels": sent}


class Scheduler:
    """Arka planda 30 saniyede bir vadesi gelen işleri çalıştırır."""

    def __init__(self, store: Optional[JobStore] = None, profiles: Optional[ProfileStore] = None, interval: int = 30):
        self.store = store or JobStore()
        self.profiles = profiles or ProfileStore()
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._tick_lock = threading.Lock()

    def tick(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Vadesi gelen işleri sırayla çalıştırır (aynı anda tek tick)."""
        if not self._tick_lock.acquire(blocking=False):
            return []
        try:
            outcomes = []
            for job in self.store.due(now or _now()):
                try:
                    outcomes.append({"job": job["name"], **run_job(job, self.store, self.profiles, now)})
                except Exception as exc:
                    logger.exception("İş çalıştırılamadı: %s", job["name"])
                    outcomes.append({"job": job["name"], "status": "error", "message": str(exc)})
            return outcomes
        finally:
            self._tick_lock.release()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                self.tick()
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=loop, name="niq-scheduler", daemon=True)
        self._thread.start()
        logger.info("Zamanlayıcı başladı")

    def stop(self) -> None:
        self._stop.set()


_scheduler: Optional[Scheduler] = None


def start_background_services() -> Scheduler:
    """Masaüstü ve web uygulaması açılışında çağrılır; tek zamanlayıcı başlatır."""
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    _scheduler.start()
    return _scheduler


def wait_forever(scheduler: Scheduler) -> None:  # niq_cli jobs serve
    scheduler.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        scheduler.stop()
