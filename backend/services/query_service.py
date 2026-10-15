"""
Soru -> SQL -> doğrula -> çalıştır akışı. API ve CLI aynı mantığı kullanır.
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.config.config import Config, Settings
from backend.core.sql_guard import validate_sql
from backend.core.utils import ValidationError
from backend.db.connector import Connector
from backend.models.models import SavedQuery
from backend.services.ai_service import AIService
from backend.services.history import HistoryStore

logger = logging.getLogger(__name__)

SMALL_SCHEMA = 6  # bu kadar ya da daha az tablo varsa ad eşleşmezse hepsi önerilir


def run_question(
    connector: Connector, ai: AIService, history: HistoryStore, question: str, tables: List[str],
) -> Tuple[Dict[str, Any], int]:
    """Soruyu cevaplar. Dönüş: (yanıt gövdesi, HTTP durum kodu). AI hataları fırlatılır."""
    if not question:
        raise ValidationError("Soru gerekli")
    if not tables:
        raise ValidationError("En az bir tablo seçilmeli")
    if len(tables) > Config.MAX_TABLES_PER_QUERY:
        raise ValidationError(f"En fazla {Config.MAX_TABLES_PER_QUERY} tablo seçilebilir")

    known = set(connector.get_tables())
    missing = [t for t in tables if t not in known]
    if missing:
        raise ValidationError("Veritabanında bulunamayan tablo(lar): " + ", ".join(missing))

    schema = connector.get_database_schema(tables)
    settings = Settings.load()
    examples = history.examples(connector.db_key, tables)
    dialect = connector.dialect

    previous_sql = previous_error = None
    sql = explanation = ""
    response = None
    attempts = 0
    for attempts in range(1, Config.REPAIR_ATTEMPTS + 2):
        answer = ai.generate_sql(question, schema, examples, previous_sql, previous_error, dialect=dialect)
        sql, explanation = answer["sql"], answer["explanation"]

        guard = validate_sql(sql, allowed_tables=tables, dialect_id=dialect.id, database=connector.profile.database)
        if not guard.ok:
            logger.warning("SQL guard reddetti (deneme %d): %s", attempts, guard.error)
            previous_sql, previous_error, response = sql, guard.error, None
            continue
        sql = guard.sql

        response = connector.execute_readonly(sql, int(settings["row_limit"]))
        if response.is_successful:
            break
        previous_sql, previous_error = sql, response.error

    success = response is not None and response.is_successful
    error_text = None if success else (previous_error or "Sorgu üretilemedi")

    analysis = None
    if success and settings.get("ai_analysis") and response.results:
        analysis = ai.analyze_results(question, sql, response.results)

    query_id = history.save(connector.db_key, SavedQuery(
        question=question, sql_query=sql, tables_used=tables, is_successful=success,
        error_message=error_text, query_results=response.results if success else None,
        result_message=explanation or None,
    ))

    payload: Dict[str, Any] = {
        "success": success, "sql": sql, "explanation": explanation, "attempts": attempts, "query_id": query_id,
    }
    if success:
        payload.update(results=response.results, row_count=len(response.results),
                       truncated=response.truncated, analysis=analysis)
        return payload, 200
    payload["error"] = error_text
    return payload, 400


def run_sql(connector: Connector, sql: str, tables: Optional[List[str]] = None) -> Dict[str, Any]:
    """Elle yazılmış SQL'i aynı guard ve salt-okunur çalıştırmayla çalıştırır (agent/CLI için)."""
    allowed = tables if tables else connector.get_tables()
    guard = validate_sql(sql, allowed_tables=allowed, dialect_id=connector.dialect.id,
                         database=connector.profile.database)
    if not guard.ok:
        return {"success": False, "sql": guard.sql, "error": guard.error}
    response = connector.execute_readonly(guard.sql, int(Settings.load()["row_limit"]))
    if not response.is_successful:
        return {"success": False, "sql": guard.sql, "error": response.error}
    return {"success": True, "sql": guard.sql, "results": response.results,
            "row_count": len(response.results), "truncated": response.truncated}


def _words(text: str) -> List[str]:
    return [w for w in re.split(r"[^\w]+", text.lower()) if len(w) >= 3]


def _stem(word: str) -> str:
    """Çok kaba Türkçe/İngilizce ek kırpma: 'siparişleri' ~ 'siparis', 'orders' ~ 'order'."""
    table = str.maketrans("çğıöşü", "cgiosu")
    word = word.lower().translate(table)
    return word[:5] if len(word) > 5 else word.rstrip("s")


def suggest_tables(connector: Connector, history: HistoryStore, question: str, limit: int = 4) -> List[str]:
    """Sorudan ilgili tabloları tahmin eder: ad benzerliği ve geçmişte aynı kelimelerle kullanılan tablolar."""
    stems = {_stem(w) for w in _words(question)}
    if not stems:
        return []
    scores: Dict[str, float] = {}
    all_tables = connector.get_tables()
    for table in all_tables:
        parts = re.split(r"[_.\s]+|(?<=[a-z])(?=[A-Z])", table)
        table_stems = {_stem(p) for p in parts if len(p) >= 3}
        hits = len(stems & table_stems)
        if hits:
            scores[table] = float(hits)
    for past in history.list(connector.db_key, limit=50):
        if not past.is_successful:
            continue
        overlap = len(stems & {_stem(w) for w in _words(past.question)})
        if overlap >= 2:
            for table in past.tables_used:
                scores[table] = scores.get(table, 0) + 0.5 * overlap
    if not scores and len(all_tables) <= SMALL_SCHEMA:
        return all_tables  # küçük şemada ad eşleşmesi (örn. Türkçe soru, İngilizce tablo adı) yoksa hepsi aday
    return [t for t, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:limit]]
