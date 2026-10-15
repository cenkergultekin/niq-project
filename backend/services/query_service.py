"""
Soru -> (tablo seçimi) -> SQL -> doğrula -> çalıştır akışı. API, CLI ve MCP aynı mantığı kullanır.
"""
import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.config.config import Config, Settings
from backend.core.sql_guard import validate_sql
from backend.core.utils import ValidationError
from backend.db.connector import Connector
from backend.models.models import SavedQuery
from backend.services.ai_service import AIService
from backend.services.history import HistoryStore
from backend.services.knowledge import KnowledgeStore, stems

logger = logging.getLogger(__name__)

SMALL_SCHEMA = 12      # bu kadar ya da daha az tabloda şemanın tamamı modele verilir
MAX_PICKED_TABLES = 8  # çok tabloda seçilen tablo üst sınırı (FK komşuları dahil)
MAX_TURNS = 6          # konuşmada modele geri verilen son tur sayısı


# --- konuşma -------------------------------------------------------------------------
@dataclass
class Turn:
    question: str
    sql: str
    explanation: str
    tables: List[str]
    columns: List[str] = field(default_factory=list)
    row_count: int = 0


@dataclass
class Conversation:
    id: str
    turns: List[Turn] = field(default_factory=list)


class ConversationStore:
    """Bellekte tutulan kısa konuşma bağlamı (son 20 konuşma)."""

    def __init__(self, capacity: int = 20):
        self._items: "OrderedDict[str, Conversation]" = OrderedDict()
        self._capacity = capacity
        self._lock = threading.Lock()

    def get(self, conversation_id: Optional[str]) -> Optional[Conversation]:
        if not conversation_id:
            return None
        with self._lock:
            conversation = self._items.get(conversation_id)
            if conversation is None:
                conversation = Conversation(conversation_id)
                self._items[conversation_id] = conversation
                while len(self._items) > self._capacity:
                    self._items.popitem(last=False)
            else:
                self._items.move_to_end(conversation_id)
            return conversation

    def reset(self, conversation_id: str) -> None:
        with self._lock:
            self._items.pop(conversation_id, None)


# --- tablo seçimi ---------------------------------------------------------------------
def _name_scores(connector: Connector, history: HistoryStore, knowledge: KnowledgeStore, question: str) -> Dict[str, float]:
    """Tablo adı, tablo notu ve geçmiş eşleşmesine göre puan."""
    wanted = stems(question)
    scores: Dict[str, float] = {}
    if not wanted:
        return scores
    notes = knowledge.get_notes(connector.db_key)
    for table in connector.get_tables():
        parts = re.split(r"[_.\s]+|(?<=[a-z])(?=[A-Z])", table)
        part_stems = set()
        for part in parts:
            if len(part) >= 3:
                part_stems |= stems(part)
        hits = float(len(wanted & part_stems))
        hits += 0.5 * len(wanted & stems(notes.get(table, {}).get("note", "")))
        if hits:
            scores[table] = hits
    for past in history.list(connector.db_key, limit=50):
        if past.is_successful and len(wanted & stems(past.question)) >= 2:
            for table in past.tables_used:
                scores[table] = scores.get(table, 0) + (1.5 if past.verified else 0.5)
    return scores


def _with_neighbors(connector: Connector, chosen: List[str], limit: int = MAX_PICKED_TABLES) -> List[str]:
    """Seçilen tabloların yabancı anahtar komşularını (bir adım) ekler, üst sınıra kadar."""
    result = list(dict.fromkeys(chosen))
    known = set(connector.get_tables())
    for table in list(result):
        if len(result) >= limit:
            break
        try:
            info = connector.get_table_info(table)
        except Exception:
            continue
        for _, target, _ in info.foreign_keys:
            if target in known and target not in result and len(result) < limit:
                result.append(target)
    return result


def resolve_tables(connector: Connector, ai: AIService, history: HistoryStore, knowledge: KnowledgeStore,
                   question: str, conversation: Optional[Conversation] = None) -> List[str]:
    """Kullanıcıya tablo sormadan ilgili tabloları bulur."""
    all_tables = connector.get_tables()
    if not all_tables:
        raise ValidationError("Veritabanında tablo bulunamadı")
    if len(all_tables) <= SMALL_SCHEMA:
        return all_tables

    previous: List[str] = []
    if conversation and conversation.turns:
        previous = [t for t in conversation.turns[-1].tables if t in all_tables]
    scores = _name_scores(connector, history, knowledge, question)
    ranked = [t for t, _ in sorted(scores.items(), key=lambda kv: -kv[1])][:4]
    if not ranked and not previous:
        notes = knowledge.get_notes(connector.db_key)
        catalog = [f"{name}: {notes[name]['note']}" if name in notes else name for name in all_tables[:300]]
        ranked = [t for t in ai.pick_tables(question, catalog) if t in all_tables]
    chosen = list(dict.fromkeys(previous + ranked))
    if not chosen:
        raise ValidationError("Soruyla ilgili tablo bulunamadı. Sorunuzu tablo veya iş terimleriyle yeniden ifade edin.")
    return _with_neighbors(connector, chosen)


def suggest_tables(connector: Connector, history: HistoryStore, question: str, limit: int = 4,
                   knowledge: Optional[KnowledgeStore] = None) -> List[str]:
    """Arayüz/CLI için ilgili tablo tahmini (AI çağırmaz)."""
    knowledge = knowledge or KnowledgeStore()
    all_tables = connector.get_tables()
    scores = _name_scores(connector, history, knowledge, question)
    if not scores and len(all_tables) <= SMALL_SCHEMA:
        return all_tables
    return [t for t, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:limit]]


# --- ana akış --------------------------------------------------------------------------
def run_question(
    connector: Connector, ai: AIService, history: HistoryStore, knowledge: KnowledgeStore, question: str,
    tables: Optional[List[str]] = None, conversation: Optional[Conversation] = None,
) -> Tuple[Dict[str, Any], int]:
    """Soruyu cevaplar. tables verilmezse otomatik seçilir. Dönüş: (yanıt gövdesi, HTTP durum kodu)."""
    if not question:
        raise ValidationError("Soru gerekli")
    known = set(connector.get_tables())
    auto = not tables
    if tables:
        if len(tables) > Config.MAX_TABLES_PER_QUERY:
            raise ValidationError(f"En fazla {Config.MAX_TABLES_PER_QUERY} tablo seçilebilir")
        missing = [t for t in tables if t not in known]
        if missing:
            raise ValidationError("Veritabanında bulunamayan tablo(lar): " + ", ".join(missing))
    else:
        tables = resolve_tables(connector, ai, history, knowledge, question, conversation)

    provided = list(tables)
    schema = connector.get_database_schema(provided)
    settings = Settings.load()
    examples = history.examples(connector.db_key, question)
    dialect = connector.dialect
    turns = conversation.turns[-MAX_TURNS:] if conversation else []

    previous_sql = previous_error = None
    sql = explanation = ""
    follow_ups: List[str] = []
    response = None
    used: List[str] = list(provided)
    attempts = 0
    for attempts in range(1, Config.REPAIR_ATTEMPTS + 2):
        context = knowledge.prompt_context(connector.db_key, provided, question, bool(settings.get("ai_value_samples")))
        answer = ai.generate_sql(question, schema, examples, previous_sql, previous_error, dialect=dialect,
                                 context=context, turns=turns)
        sql, explanation = answer["sql"], answer["explanation"]
        follow_ups = answer.get("follow_ups", [])

        guard = validate_sql(sql, allowed_tables=sorted(known), dialect_id=dialect.id, database=connector.profile.database)
        if not guard.ok:
            logger.warning("SQL guard reddetti (deneme %d): %s", attempts, guard.error)
            previous_sql, previous_error, response = sql, guard.error, None
            continue
        sql = guard.sql
        used = _display_names(guard.tables or set(), known) or provided

        # Model şemada verilmeyen bir tabloya başvurduysa tanımını ekleyip yeniden dene
        unseen = [t for t in used if t not in provided]
        if unseen and attempts <= Config.REPAIR_ATTEMPTS:
            provided += unseen
            schema = connector.get_database_schema(provided)
            previous_sql = sql
            previous_error = ("The query used tables whose definitions were not provided: " + ", ".join(unseen)
                              + ". Their definitions are now in the schema; rewrite the query using the exact column names.")
            response = None
            continue

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
        question=question, sql_query=sql, tables_used=used, is_successful=success,
        error_message=error_text, query_results=response.results if success else None,
        result_message=explanation or None,
    ))

    if success and conversation is not None:
        columns = list(response.results[0].keys()) if response.results else []
        conversation.turns.append(Turn(question, sql, explanation, used, columns, len(response.results)))

    payload: Dict[str, Any] = {
        "success": success, "sql": sql, "explanation": explanation, "attempts": attempts, "query_id": query_id,
        "tables_used": used, "auto_tables": auto, "follow_ups": follow_ups if success else [],
    }
    if success:
        payload.update(results=response.results, row_count=len(response.results),
                       truncated=response.truncated, analysis=analysis)
        return payload, 200
    payload["error"] = error_text
    return payload, 400


def _display_names(used: set, known: set) -> List[str]:
    """Guard'ın küçük harfli tablo adlarını veritabanındaki gösterim adlarına eşler."""
    lookup = {}
    for name in known:
        lookup[name.lower()] = name
        lookup["dbo." + name.lower()] = name
    found = set()
    for key in used:
        if key in lookup:
            found.add(lookup[key])
        elif "." in key and key.split(".", 1)[1] in lookup:  # varsayılan şema öneki (public.orders gibi)
            found.add(lookup[key.split(".", 1)[1]])
    return sorted(found)


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
