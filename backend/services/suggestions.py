"""
Hazır sorular: bağlanınca boş kutuyla karşılaşmamak için şemadan ve iş sözlüğünden üretilen tıkla-çalıştır sorular.
"""
import logging
from typing import Any, Dict, List

from backend.db.connector import Connector
from backend.services.ai_service import AIService, AIServiceError
from backend.services.knowledge import KnowledgeStore

logger = logging.getLogger(__name__)

CATALOG_TABLES = 15
CATALOG_COLUMNS = 10


def _catalog(connector: Connector, knowledge: KnowledgeStore) -> str:
    notes = knowledge.get_notes(connector.db_key)
    lines: List[str] = []
    for table in connector.get_tables()[:CATALOG_TABLES]:
        try:
            info = connector.get_table_info(table)
        except Exception:
            continue
        columns = ", ".join(f"{c.name} {c.data_type}" for c in info.columns[:CATALOG_COLUMNS])
        note = f" -- {notes[table]['note']}" if table in notes else ""
        lines.append(f"{table}({columns}){note}")
    terms = knowledge.list_glossary(connector.db_key)[:10]
    if terms:
        lines.append("Business terms: " + "; ".join(f"{t['term']} = {t['definition']}" for t in terms))
    return "\n".join(lines)


def schema_questions(connector: Connector, count: int = 6) -> List[str]:
    """AI olmadan: tablo ve kolon adlarından genel, her veritabanında anlamlı sorular."""
    questions: List[str] = []
    for table in connector.get_tables()[:count]:
        questions.append(f"{table} tablosunda kaç kayıt var?")
        try:
            info = connector.get_table_info(table)
        except Exception:
            continue
        numeric = next((c.name for c in info.columns
                        if any(t in c.data_type.lower() for t in ("int", "decimal", "numeric", "money", "float", "real"))
                        and c.name not in info.primary_key and not c.name.lower().endswith("id")), None)
        if numeric:
            questions.append(f"{table} tablosunda {numeric} toplamı ve ortalaması nedir?")
        if len(questions) >= count:
            break
    return questions[:count]


def build_suggestions(connector: Connector, ai: AIService, knowledge: KnowledgeStore, refresh: bool = False) -> Dict[str, Any]:
    key = connector.db_key
    if not refresh:
        cached = knowledge.get_suggestions(key)
        if cached and (cached["source"] == "ai" or not ai.ai_available):
            return cached
    if ai.ai_available:
        try:
            questions = ai.suggest_questions(_catalog(connector, knowledge))
            if questions:
                knowledge.save_suggestions(key, questions, "ai")
                return {"questions": questions, "source": "ai"}
        except AIServiceError as exc:
            logger.info("Hazır sorular AI ile üretilemedi: %s", exc)
    questions = schema_questions(connector)
    if questions:
        knowledge.save_suggestions(key, questions, "schema")
    return {"questions": questions, "source": "schema"}
