"""Kullanıcının seçtiği DB kapsamı içinde soru bazlı kaynak ve tablo seçimi."""
from backend.core.utils import ValidationError
from backend.services.analyst import run_analysis
from backend.services.query_service import run_question


def query_workspace(workspace, ai, history, knowledge, conversations, question, conversation_id, analyze=False):
    catalog = [{"id": key, "name": item["label"], "tables": item["tables"][:150],
                "recent_question": item.get("last_question", "") if item.get("last_conversation") == conversation_id else ""}
               for key, item in workspace.items()]
    picked = ai.pick_databases(question, catalog) if len(catalog) > 1 else [catalog[0]["id"]]
    chosen = [key for key in dict.fromkeys(picked) if key in workspace]
    if not chosen:
        raise ValidationError("Seçilen veritabanlarında bu soruyla ilgili veri bulunamadı. Sorunu biraz daha açık yaz.")
    results = []
    for key in chosen:
        item = workspace[key]
        conversation = conversations.get(f"{conversation_id}:{key}") if conversation_id else None
        if analyze:
            payload = run_analysis(item["connector"], ai, history, knowledge, question, conversation)
            status = 200
        else:
            payload, status = run_question(item["connector"], ai, history, knowledge, question, None, conversation)
        results.append({"source": item["label"], "data": payload, "status": status})
        item["last_question"] = question
        item["last_conversation"] = conversation_id
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
