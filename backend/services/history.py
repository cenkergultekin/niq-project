"""
Sorgu geçmişi: yerel SQLite (%APPDATA%/NIQ/history.db).

Önceden geçmiş kullanıcının kendi veritabanına `saved_queries` tablosu açılarak
tutuluyordu; bu hem yazma yetkisi gerektiriyor hem de salt-okunur kullanıcıyla
çalışmayı engelliyordu. Artık geçmiş yerelde, bağlantı (sunucu|veritabanı)
bazında ayrı tutulur.
"""
import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from backend.config.config import Config, data_dir
from backend.models.models import SavedQuery

logger = logging.getLogger(__name__)


def _json_default(obj: Any):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    return str(obj)


class HistoryStore:
    def __init__(self, path: Optional[str] = None):
        self.path = str(path or data_dir() / "history.db")
        self._lock = threading.Lock()
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS queries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    db_key TEXT NOT NULL,
                    question TEXT NOT NULL,
                    sql_query TEXT NOT NULL,
                    tables_used TEXT,
                    created_at TEXT NOT NULL,
                    is_successful INTEGER NOT NULL,
                    error_message TEXT,
                    query_results TEXT,
                    result_message TEXT
                )"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queries_db ON queries(db_key, created_at DESC)")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(queries)")}
            if "verified" not in columns:
                conn.execute("ALTER TABLE queries ADD COLUMN verified INTEGER NOT NULL DEFAULT 0")

    def save(self, db_key: str, query: SavedQuery) -> int:
        rows = query.query_results
        payload = json.dumps(rows[: Config.HISTORY_MAX_ROWS], ensure_ascii=False, default=_json_default) if rows is not None else None
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO queries (db_key, question, sql_query, tables_used, created_at,
                       is_successful, error_message, query_results, result_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (db_key, query.question, query.sql_query, json.dumps(query.tables_used, ensure_ascii=False),
                 query.created_at.isoformat(), int(query.is_successful), query.error_message,
                 payload, query.result_message),
            )
            return int(cur.lastrowid)

    @staticmethod
    def _row_to_query(row: sqlite3.Row) -> SavedQuery:
        def loads(value, default):
            try:
                return json.loads(value) if value else default
            except ValueError:
                return default

        return SavedQuery(
            id=row["id"],
            question=row["question"],
            sql_query=row["sql_query"],
            tables_used=loads(row["tables_used"], []),
            created_at=datetime.fromisoformat(row["created_at"]),
            is_successful=bool(row["is_successful"]),
            error_message=row["error_message"],
            query_results=loads(row["query_results"], None),
            result_message=row["result_message"],
            verified=bool(row["verified"]),
        )

    def list(self, db_key: str, limit: int = 50, offset: int = 0) -> List[SavedQuery]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM queries WHERE db_key = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (db_key, limit, offset),
            ).fetchall()
        return [self._row_to_query(r) for r in rows]

    def get(self, db_key: str, query_id: int) -> Optional[SavedQuery]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM queries WHERE db_key = ? AND id = ?", (db_key, query_id)).fetchone()
        return self._row_to_query(row) if row else None

    def delete(self, db_key: str, query_id: int) -> bool:
        with self._lock, self._connect() as conn:
            return conn.execute("DELETE FROM queries WHERE db_key = ? AND id = ?", (db_key, query_id)).rowcount > 0

    def set_verified(self, db_key: str, query_id: int, verified: bool) -> bool:
        with self._lock, self._connect() as conn:
            return conn.execute("UPDATE queries SET verified = ? WHERE db_key = ? AND id = ?",
                                (int(verified), db_key, query_id)).rowcount > 0

    def update_sql(self, db_key: str, query_id: int, sql: str, tables: List[str], success: bool,
                   error: Optional[str], results: Optional[List[Dict[str, Any]]]) -> bool:
        """Kullanıcı SQL'i düzeltip çalıştırınca kaydı günceller; düzeltilmiş ve çalışan sorgu doğrulanmış sayılır."""
        payload = json.dumps(results[: Config.HISTORY_MAX_ROWS], ensure_ascii=False, default=_json_default) if results is not None else None
        with self._lock, self._connect() as conn:
            return conn.execute(
                """UPDATE queries SET sql_query = ?, tables_used = ?, is_successful = ?, error_message = ?,
                       query_results = ?, verified = ? WHERE db_key = ? AND id = ?""",
                (sql, json.dumps(tables, ensure_ascii=False), int(success), error, payload, int(success), db_key, query_id),
            ).rowcount > 0

    def examples(self, db_key: str, question: str, limit: int = 3) -> List[SavedQuery]:
        """Few-shot örnekler: önce doğrulanmışlar, soruyla kelime örtüşmesi en yüksek olanlar. Tablo seçimi gerekmez."""
        from backend.services.knowledge import stems
        wanted = stems(question)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM queries WHERE db_key = ? AND is_successful = 1 ORDER BY id DESC LIMIT 200", (db_key,),
            ).fetchall()
        scored = []
        for row in rows:
            query = self._row_to_query(row)
            overlap = len(wanted & stems(query.question))
            if overlap or query.verified:
                scored.append((query.verified * 2 + overlap, query.id, query))
        scored.sort(key=lambda item: (-item[0], -item[1]))
        return [q for score, _, q in scored if score >= 2][:limit]
