"""
Çok adımlı analiz: "satışlar neden düştü?", "bu ayı geçen ayla karşılaştır" gibi tek SQL ile cevaplanmayan sorular.

Model araç çağırarak birkaç salt-okunur sorgu planlar, sonuçlara bakar, gerekirse yeni sorgu yazar ve
sayılarla desteklenmiş bir cevap verir. Her sorgu tek-atımlık akıştaki aynı güvenlik katmanından geçer.
Sonuç satırları modele gittiği için bu mod `ai_analysis` izni gerektirir (kritik adım).
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from backend.config.config import Settings
from backend.core.sql_guard import validate_sql
from backend.db.connector import Connector
from backend.models.models import SavedQuery
from backend.services.ai_service import AIService, AIServiceError, format_schema
from backend.services.history import HistoryStore
from backend.services.knowledge import KnowledgeStore
from backend.services.query_service import Conversation, resolve_tables

logger = logging.getLogger(__name__)

MAX_STEPS = 6
PREVIEW_ROWS = 30        # modele geri verilen satır sayısı
RESULT_CHARS = 3500      # modele geri verilen araç sonucu üst sınırı
STEP_ROWS_FOR_UI = 15

ANALYSIS_INTENT = re.compile(
    r"\b(neden|niçin|nicin|niye|analiz|incele|karşılaştır|karsilastir|kıyasla|kiyasla|trend|eğilim|egilim|"
    r"düşüş|dusus|artış|artis|değişim|degisim|özetle|ozetle|öne çıkan|one cikan|anomali|ne oldu|why|compare|"
    r"analy[sz]e|explain|insight|breakdown|driver)",
    re.IGNORECASE)

SYSTEM = """You are a careful data analyst working on a {sql_name} database. Answer the user's question by running
several read-only queries with the run_sql tool, looking at the results, and refining. Plan briefly, then query.

Rules:
- Only SELECT statements (CTEs allowed). Use exact table and column names from the schema; call describe_tables for other tables.
- Prefer aggregated queries (GROUP BY, TOP/LIMIT); never pull large raw dumps.
- For "why" questions break the metric down by the relevant dimensions (time, category, region, customer) and quantify each contribution.
- For comparisons compute both sides on the same basis (same period length, same filters).
- Use at most {max_steps} queries. When you have enough evidence call finish.
- In finish, write the answer in Turkish: lead with the conclusion, support it with concrete numbers from the results, mention assumptions or data limits.
  Never invent numbers that are not in the results.

Database schema (more tables available through describe_tables):
{schema}
"""

TOOLS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "run_sql",
        "description": "Run one read-only SELECT query and get the first rows back.",
        "parameters": {"type": "object", "properties": {
            "purpose": {"type": "string", "description": "What this query is meant to find out"},
            "sql": {"type": "string", "description": "A single SELECT statement"}},
            "required": ["purpose", "sql"]}}},
    {"type": "function", "function": {
        "name": "describe_tables",
        "description": "Get columns, primary and foreign keys for tables not already in the schema.",
        "parameters": {"type": "object", "properties": {
            "tables": {"type": "array", "items": {"type": "string"}}}, "required": ["tables"]}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": "Give the final answer to the user.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "Short title (max 8 words)"},
            "answer": {"type": "string", "description": "Turkish answer with concrete numbers"},
            "findings": {"type": "array", "items": {"type": "string"}, "description": "2-5 key findings, one line each"}},
            "required": ["title", "answer", "findings"]}}},
]


def is_analytical(question: str) -> bool:
    """Soru çok adımlı analiz gerektiriyor gibi mi? (otomatik mod için sezgi)"""
    return bool(ANALYSIS_INTENT.search(question or ""))


def _tool_result(step: Dict[str, Any]) -> str:
    """Araç sonucu: ilk satırlar, üst sınıra sığana kadar azaltılır."""
    rows = step["rows"][:PREVIEW_ROWS]
    while True:
        text = json.dumps({"columns": step["columns"], "row_count": step["row_count"],
                           "truncated": step["truncated"] or len(rows) < step["row_count"], "rows": rows},
                          ensure_ascii=False, default=str)
        if len(text) <= RESULT_CHARS or len(rows) <= 1:
            return text[:RESULT_CHARS * 2]
        rows = rows[: max(1, len(rows) // 2)]


def _run_step(connector: Connector, sql: str, known: List[str], row_limit: int) -> Dict[str, Any]:
    guard = validate_sql(sql, allowed_tables=known, dialect_id=connector.dialect.id, database=connector.profile.database)
    if not guard.ok:
        return {"ok": False, "sql": sql, "error": guard.error}
    response = connector.execute_readonly(guard.sql, row_limit)
    if not response.is_successful:
        return {"ok": False, "sql": guard.sql, "error": response.error}
    rows = response.results or []
    return {"ok": True, "sql": guard.sql, "rows": rows, "columns": list(rows[0].keys()) if rows else [],
            "row_count": len(rows), "truncated": response.truncated}


def run_analysis(connector: Connector, ai: AIService, history: HistoryStore, knowledge: KnowledgeStore,
                 question: str, conversation: Optional[Conversation] = None, max_steps: int = MAX_STEPS) -> Dict[str, Any]:
    """Soruyu birkaç sorguyla analiz eder. Dönüş: UI ve API için yanıt gövdesi."""
    tables = resolve_tables(connector, ai, history, knowledge, question, conversation)
    schema = connector.get_database_schema(tables)
    known = sorted(connector.get_tables())
    settings = Settings.load()
    row_limit = max(PREVIEW_ROWS, min(int(settings["row_limit"]), 1000))

    context = knowledge.prompt_context(connector.db_key, tables, question, bool(settings.get("ai_value_samples")))
    system = SYSTEM.format(sql_name=connector.dialect.sql_name, max_steps=max_steps, schema=format_schema(schema))
    if context:
        system += "\nBusiness knowledge:\n" + context + "\n"
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    for turn in (conversation.turns[-4:] if conversation else []):
        messages.append({"role": "user", "content": turn.question})
        messages.append({"role": "assistant", "content": f"{turn.explanation} (SQL: {turn.sql})"})
    messages.append({"role": "user", "content": question})

    steps: List[Dict[str, Any]] = []
    final: Optional[Dict[str, Any]] = None
    queries_used = 0
    last_rows: Optional[Dict[str, Any]] = None

    for _ in range(max_steps + 3):
        message = ai.chat(messages, TOOLS)
        calls = list(message.tool_calls or [])
        assistant: Dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        if getattr(message, "response_items", None) is not None:
            assistant["_response_items"] = message.response_items
        if getattr(message, "anthropic_blocks", None) is not None:
            assistant["_anthropic_blocks"] = message.anthropic_blocks
        if calls:
            assistant["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in calls]
        messages.append(assistant)

        if not calls:  # araç çağırmadan düz cevap verdi: son cevap sayılır
            text = (message.content or "").strip()
            if text:
                final = {"title": "Analiz", "answer": text, "findings": []}
            break

        for call in calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except ValueError:
                args = {}
            if name == "finish":
                final = {"title": str(args.get("title", "Analiz")), "answer": str(args.get("answer", "")),
                         "findings": [str(f) for f in args.get("findings", [])][:6]}
                result = "ok"
            elif name == "describe_tables":
                wanted = [t for t in args.get("tables", []) if t in known][:6]
                try:
                    result = format_schema(connector.get_database_schema(wanted)) or "no matching tables"
                except Exception as exc:
                    result = f"error: {exc}"
            elif name == "run_sql":
                queries_used += 1
                if queries_used > max_steps:
                    result = "error: query limit reached; call finish now with what you have"
                else:
                    step = _run_step(connector, str(args.get("sql", "")), known, row_limit)
                    steps.append({
                        "purpose": str(args.get("purpose", "")), "sql": step["sql"], "ok": step["ok"],
                        "error": step.get("error"), "row_count": step.get("row_count", 0),
                        "columns": step.get("columns", []), "rows": step.get("rows", [])[:STEP_ROWS_FOR_UI]})
                    if step["ok"]:
                        last_rows = step
                        result = _tool_result(step)
                    else:
                        result = f"error: {step['error']}"
            else:
                result = f"error: unknown tool {name}"
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

        if final:
            break

    if not final:
        raise AIServiceError("Analiz tamamlanamadı: model bir sonuç üretmedi.")

    ok_steps = [s for s in steps if s["ok"]]
    success = bool(final["answer"])
    last_sql = ok_steps[-1]["sql"] if ok_steps else ""
    query_id = history.save(connector.db_key, SavedQuery(
        question=question, sql_query=last_sql or "-", tables_used=tables, is_successful=success,
        error_message=None if success else "Analiz sonuç üretmedi",
        query_results=last_rows["rows"] if last_rows else None, result_message=final["answer"][:2000]))
    if conversation is not None and success:
        from backend.services.query_service import Turn
        conversation.turns.append(Turn(question, last_sql, final["answer"][:300], tables,
                                       last_rows["columns"] if last_rows else [], last_rows["row_count"] if last_rows else 0))

    payload: Dict[str, Any] = {
        "success": success, "mode": "analysis", "title": final["title"], "answer": final["answer"],
        "findings": final["findings"], "steps": steps, "tables_used": tables, "query_id": query_id,
        "sql": last_sql, "explanation": final["answer"],
    }
    if last_rows:
        payload.update(results=last_rows["rows"], row_count=last_rows["row_count"], truncated=last_rows["truncated"])
    return payload
