"""Kullanıcının seçtiği DB kapsamı içinde soru bazlı kaynak ve tablo seçimi."""
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from backend.core.utils import ValidationError
from backend.services.ai_service import query_budget
from backend.services.analyst import run_analysis
from backend.services.query_service import run_question

logger = logging.getLogger(__name__)


@query_budget
def query_workspace(workspace, ai, history, knowledge, conversations, question, conversation_id, analyze=False):
    started = time.monotonic()
    catalog = [{"id": key, "name": item["label"], "tables": item["tables"][:150],
                "recent_question": item.get("last_question", "") if item.get("last_conversation") == conversation_id else ""}
               for key, item in workspace.items()]
    picked = ai.pick_databases(question, catalog) if len(catalog) > 1 else [catalog[0]["id"]]
    chosen = [key for key in dict.fromkeys(picked) if key in workspace]
    if not chosen:
        raise ValidationError("Seçilen veritabanlarında bu soruyla ilgili veri bulunamadı. Sorunu biraz daha açık yaz.")
    def query_source(key):
        source_started = time.monotonic()
        item = workspace[key]
        conversation = conversations.get(f"{conversation_id}:{key}") if conversation_id else None
        if analyze:
            payload = run_analysis(item["connector"], ai, history, knowledge, question, conversation)
            status = 200
        else:
            payload, status = run_question(item["connector"], ai, history, knowledge, question, None, conversation)
        item["last_question"] = question
        item["last_conversation"] = conversation_id
        logger.info("Source query completed (%.1fs, status=%d)", time.monotonic() - source_started, status)
        return {"source": item["label"], "data": payload, "status": status}

    if len(chosen) == 1:
        results = [query_source(chosen[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(4, len(chosen)), thread_name_prefix="niq-query") as pool:
            futures = [pool.submit(copy_context().run, query_source, key) for key in chosen]
            results = [future.result() for future in futures]
    logger.info("Workspace query completed (%.1fs, sources=%d)", time.monotonic() - started, len(results))
    if len(results) == 1:
        payload = results[0]["data"]
        payload["source"] = results[0]["source"]
        return payload, results[0]["status"]
    # Kaynakları ayrı tut: birbirinden farklı DB sonuçları sessizce toplanmaz veya JOIN edilmez.
    rows = []
    for result in results:
        for row in result["data"].get("results", []):
            rows.append({**row, "NIQ kaynağı": result["source"]})
    columns = list(dict.fromkeys(["NIQ kaynağı", *(column for row in rows for column in row)]))
    rows = [{column: row.get(column) for column in columns} for row in rows]
    failures = [result for result in results if not result["data"].get("success")]
    return {"success": not failures, "results": rows, "row_count": len(rows), "mode": "workspace",
            "explanation": f"{len(results)} veritabanının sonuçları kaynaklarıyla gösteriliyor.",
            "sql": "\n\n".join(f"-- {result['source']}\n{result['data'].get('sql', '')}" for result in results),
            "sources": results, "query_id": None,
            "error": "; ".join(f"{result['source']}: {result['data'].get('error', 'Sorgu başarısız')}" for result in failures) if failures else None,
            "tables_used": [], "follow_ups": [], "attempts": 1}, 400 if failures else 200
