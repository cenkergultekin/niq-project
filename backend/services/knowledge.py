"""
Bilgi katmanı: iş sözlüğü, tablo/kolon notları ve veri profili.

Modelin en çok yanıldığı yer SQL sözdizimi değil anlamdır ("aktif müşteri" kimdir, "net ciro" nasıl
hesaplanır, `status` içindeki `A/P/C` ne demek). Bu bilgi bağlantı bazında yerelde
(`%APPDATA%/NIQ/knowledge.db`) saklanır ve her soruda AI istemine eklenir.
"""
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from backend.config.config import data_dir

# Değerleri AI'a gönderilmeyecek kişisel veri gibi görünen kolon adları
PII_COLUMN = re.compile(
    r"(mail|phone|telefon|gsm|iban|tckn|kimlik|password|parola|sifre|şifre|token|secret|address|adres|"
    r"(^|_)ad$|soyad|first_?name|last_?name|full_?name|ssn|card|kart|birth|dogum|doğum)", re.IGNORECASE)


def words(text: str) -> List[str]:
    table = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    return [w for w in re.split(r"[^\w]+", text.translate(table).lower()) if len(w) >= 3]


def stem(word: str) -> str:
    """Kaba Türkçe/İngilizce ek kırpma: 'siparişleri' ~ 'sipar', 'orders' ~ 'order'."""
    return word[:5] if len(word) > 5 else word.rstrip("s")


def stems(text: str) -> set:
    return {stem(w) for w in words(text)}


class KnowledgeStore:
    def __init__(self, path: Optional[str] = None):
        self.path = str(path or data_dir() / "knowledge.db")
        self._lock = threading.Lock()
        with self._lock, self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS glossary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, db_key TEXT NOT NULL, term TEXT NOT NULL,
                    definition TEXT NOT NULL, sql_hint TEXT, created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_glossary_db ON glossary(db_key);
                CREATE TABLE IF NOT EXISTS table_notes (
                    db_key TEXT NOT NULL, table_name TEXT NOT NULL, note TEXT NOT NULL,
                    auto INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (db_key, table_name));
                CREATE TABLE IF NOT EXISTS suggestions (
                    db_key TEXT PRIMARY KEY, questions_json TEXT NOT NULL, source TEXT NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS value_profiles (
                    db_key TEXT NOT NULL, table_name TEXT NOT NULL, column_name TEXT NOT NULL,
                    values_json TEXT NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY (db_key, table_name, column_name));
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

    # --- sözlük ------------------------------------------------------------
    def list_glossary(self, db_key: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id, term, definition, sql_hint FROM glossary WHERE db_key = ? ORDER BY term COLLATE NOCASE",
                                (db_key,)).fetchall()
        return [dict(r) for r in rows]

    def add_term(self, db_key: str, term: str, definition: str, sql_hint: str = "") -> int:
        term, definition = term.strip(), definition.strip()
        if not term or not definition:
            raise ValueError("Terim ve tanım gerekli")
        with self._lock, self._connect() as conn:
            existing = conn.execute("SELECT id FROM glossary WHERE db_key = ? AND lower(term) = lower(?)",
                                    (db_key, term)).fetchone()
            if existing:
                conn.execute("UPDATE glossary SET definition = ?, sql_hint = ? WHERE id = ?",
                             (definition, sql_hint.strip(), existing["id"]))
                return int(existing["id"])
            cur = conn.execute("INSERT INTO glossary (db_key, term, definition, sql_hint, created_at) VALUES (?,?,?,?,?)",
                               (db_key, term, definition, sql_hint.strip(), time.time()))
            return int(cur.lastrowid)

    def delete_term(self, db_key: str, term_id: int) -> bool:
        with self._lock, self._connect() as conn:
            return conn.execute("DELETE FROM glossary WHERE db_key = ? AND id = ?", (db_key, term_id)).rowcount > 0

    # --- tablo notları -----------------------------------------------------
    def get_notes(self, db_key: str) -> Dict[str, Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT table_name, note, auto FROM table_notes WHERE db_key = ?", (db_key,)).fetchall()
        return {r["table_name"]: {"note": r["note"], "auto": bool(r["auto"])} for r in rows}

    def set_note(self, db_key: str, table: str, note: str, auto: bool = False) -> None:
        with self._lock, self._connect() as conn:
            if not note.strip():
                conn.execute("DELETE FROM table_notes WHERE db_key = ? AND table_name = ?", (db_key, table))
                return
            existing = conn.execute("SELECT auto FROM table_notes WHERE db_key = ? AND table_name = ?",
                                    (db_key, table)).fetchone()
            if auto and existing and not existing["auto"]:
                return  # elle yazılmış notu otomatik taslak ezmez
            conn.execute("INSERT OR REPLACE INTO table_notes (db_key, table_name, note, auto) VALUES (?,?,?,?)",
                         (db_key, table, note.strip(), int(auto)))

    # --- veri profili ------------------------------------------------------
    def save_profile(self, db_key: str, table: str, column_values: Dict[str, List[Any]]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM value_profiles WHERE db_key = ? AND table_name = ?", (db_key, table))
            for column, values in column_values.items():
                conn.execute("INSERT INTO value_profiles VALUES (?,?,?,?,?)",
                             (db_key, table, column, json.dumps(values, ensure_ascii=False, default=str), time.time()))

    def get_profile(self, db_key: str, tables: Optional[List[str]] = None) -> Dict[str, Dict[str, List[Any]]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT table_name, column_name, values_json FROM value_profiles WHERE db_key = ?",
                                (db_key,)).fetchall()
        result: Dict[str, Dict[str, List[Any]]] = {}
        for row in rows:
            if tables is None or row["table_name"] in tables:
                result.setdefault(row["table_name"], {})[row["column_name"]] = json.loads(row["values_json"])
        return result

    def profiled_tables(self, db_key: str) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(DISTINCT table_name) FROM value_profiles WHERE db_key = ?",
                                (db_key,)).fetchone()[0]

    # --- hazır sorular önbelleği ---------------------------------------------
    def get_suggestions(self, db_key: str, max_age_hours: float = 24.0) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT questions_json, source, updated_at FROM suggestions WHERE db_key = ?", (db_key,)).fetchone()
        if not row or time.time() - row["updated_at"] > max_age_hours * 3600:
            return None
        return {"questions": json.loads(row["questions_json"]), "source": row["source"]}

    def save_suggestions(self, db_key: str, questions: List[str], source: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO suggestions VALUES (?,?,?,?)",
                         (db_key, json.dumps(questions, ensure_ascii=False), source, time.time()))

    # --- AI istemi için bağlam ---------------------------------------------
    def relevant_terms(self, db_key: str, question: str, limit: int = 20) -> List[Dict[str, Any]]:
        terms = self.list_glossary(db_key)
        if len(terms) <= limit:
            return terms
        q = stems(question)
        scored = sorted(terms, key=lambda t: -len(q & stems(t["term"] + " " + t["definition"])))
        return scored[:limit]

    def prompt_context(self, db_key: str, tables: List[str], question: str, include_values: bool) -> str:
        """İstemin 'Business knowledge' bölümü. Değerler yalnızca kullanıcı izin verdiyse eklenir."""
        lines: List[str] = []
        terms = self.relevant_terms(db_key, question)
        if terms:
            lines.append("Business glossary (follow these definitions exactly):")
            for term in terms:
                hint = f" SQL: {term['sql_hint']}" if term.get("sql_hint") else ""
                lines.append(f"- {term['term']}: {term['definition']}{hint}")
        notes = self.get_notes(db_key)
        table_lines = [f"- {t}: {notes[t]['note']}" for t in tables if t in notes]
        if table_lines:
            lines.append("Table notes:")
            lines += table_lines
        if include_values:
            profile = self.get_profile(db_key, tables)
            value_lines = []
            for table in tables:
                for column, values in profile.get(table, {}).items():
                    shown = ", ".join(repr(v) if isinstance(v, str) else str(v) for v in values[:12])
                    value_lines.append(f"- {table}.{column} values: {shown}")
            if value_lines:
                lines.append("Known categorical values (use these exact spellings in filters):")
                lines += value_lines
        return "\n".join(lines)
