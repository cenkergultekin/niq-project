"""
AI servisi: doğal dil -> T-SQL ve (isteğe bağlı) sonuç yorumu.

OpenAI, OpenRouter ve yerel OpenAI-uyumlu sunucular (Ollama, LM Studio) aynı
SDK ile konuşur. Çıktı JSON şemasıyla istenir; sağlayıcı/model şemayı
desteklemiyorsa düz metne düşülür ve toleranslı ayrıştırıcı kullanılır.
"""
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from openai import APIStatusError, BadRequestError, OpenAI

from backend.config.config import Settings
from backend.db.dialects import Dialect, get_dialect
from backend.models.models import DatabaseSchema, SavedQuery

logger = logging.getLogger(__name__)

SQL_SCHEMA = {
    "name": "sql_answer",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "Tek bir T-SQL SELECT sorgusu"},
            "explanation": {"type": "string", "description": "Sorgunun ne yaptığı, 1-2 cümle Türkçe"},
            "follow_ups": {"type": "array", "items": {"type": "string"},
                           "description": "Kullanıcının sıradaki olası 2-3 kısa takip sorusu (Türkçe)"},
        },
        "required": ["sql", "explanation", "follow_ups"],
        "additionalProperties": False,
    },
}

ANALYSIS_SCHEMA = {
    "name": "analysis",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "analysis": {"type": "string"},
        },
        "required": ["summary", "analysis"],
        "additionalProperties": False,
    },
}

TABLES_SCHEMA = {
    "name": "tables",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"tables": {"type": "array", "items": {"type": "string"}}},
        "required": ["tables"],
        "additionalProperties": False,
    },
}

_NULLABLE_INT = {"type": ["integer", "null"]}
_NULLABLE_STR = {"type": ["string", "null"]}
_NULLABLE_NUM = {"type": ["number", "null"]}

JOB_SCHEMA = {
    "name": "job_plan",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "kind": {"type": "string", "enum": ["report", "alert"]},
            "question": {"type": "string"},
            "schedule": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["interval", "daily", "weekly", "monthly"]},
                    "minutes": _NULLABLE_INT, "time": _NULLABLE_STR, "day": _NULLABLE_INT,
                },
                "required": ["kind", "minutes", "time", "day"], "additionalProperties": False,
            },
            "condition": {
                "type": ["object", "null"],
                "properties": {
                    "op": {"type": "string", "enum": [">", "<", ">=", "<=", "==", "!=", "rows>0", "rows==0"]},
                    "value": _NULLABLE_NUM, "column": _NULLABLE_STR,
                },
                "required": ["op", "value", "column"], "additionalProperties": False,
            },
            "channels": {"type": "array", "items": {"type": "string", "enum": ["inbox", "telegram", "webhook"]}},
        },
        "required": ["name", "kind", "question", "schedule", "condition", "channels"],
        "additionalProperties": False,
    },
}

SUGGEST_SCHEMA = {
    "name": "suggestions",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
        "required": ["questions"],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = """You are an expert {sql_name} analyst. Convert the user's question into ONE read-only query.

Rules:
- Use ONLY the tables and columns in the schema below; copy names exactly ({quoting}).
- Output a single SELECT statement (CTEs and UNION are allowed). Never write INSERT, UPDATE, DELETE, DDL, EXEC or temp tables.
- Use the foreign keys to JOIN tables correctly. Do not reference tables that are not listed.
- For "first/top n" questions use this dialect's row limiting syntax (TOP, LIMIT or FETCH FIRST). Use explicit column lists unless the user asks for everything.
- Give columns readable aliases. Match the language of the question in aliases when natural.
- If the question cannot be answered with the given schema, return the closest reasonable query and say so in the explanation.
- Earlier turns of this conversation may follow. When the new message is a follow-up ("bunu ülkeye göre kır", "sadece Almanya",
  "geçen yılla karşılaştır", "ilk 5"), modify the previous query accordingly instead of starting over.
- Respond as JSON: {"sql": "...", "explanation": "...", "follow_ups": ["...", "..."]}; the explanation is 1-2 sentences in Turkish;
  follow_ups are 2-3 short, natural next questions the user might ask about this result, in Turkish.

Database schema:
{schema}
"""

# Sıcaklık parametresini reddeden düşünen (reasoning) model aileleri
_NO_TEMPERATURE = re.compile(r"(^|/)(o\d|gpt-5|gpt-6)", re.IGNORECASE)


class AIUnavailableError(Exception):
    """API anahtarı/sağlayıcı yapılandırılmamış."""


class AIService:
    def __init__(self):
        self.client: Optional[OpenAI] = None
        self.config: Dict[str, Any] = {}
        self.reconfigure()

    # --- yapılandırma -----------------------------------------------------
    def reconfigure(self) -> None:
        """Ayarları (sağlayıcı, model, anahtar) yeniden okuyup istemciyi kurar."""
        self.config = Settings.ai_config()
        self.client = None
        if not self.config["api_key"]:
            logger.info("AI sağlayıcısı (%s) için API anahtarı yok", self.config["provider"])
            return
        kwargs: Dict[str, Any] = {"api_key": self.config["api_key"], "timeout": 60.0, "max_retries": 1}
        if self.config["base_url"]:
            kwargs["base_url"] = self.config["base_url"]
        if self.config["provider"] == "openrouter":
            kwargs["default_headers"] = {"X-Title": "NIQ Desktop"}
        self.client = OpenAI(**kwargs)
        logger.info("AI hazır: %s / %s", self.config["provider"], self.config["model"])

    @property
    def ai_available(self) -> bool:
        return self.client is not None

    @property
    def model(self) -> str:
        return self.config.get("model", "")

    # --- çağrı ------------------------------------------------------------
    def _build_params(self, messages: List[Dict[str, Any]], schema: Optional[Dict[str, Any]], max_tokens: Optional[int],
                      temperature: float, tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        if not self.client:
            raise AIUnavailableError("AI için API anahtarı ayarlanmamış. Ayarlar sayfasından ekleyin.")
        limit = max_tokens or self.config["max_output_tokens"]
        # Yerel sunucular eski max_tokens adını bekler; OpenAI/OpenRouter için doğru alan max_completion_tokens
        limit_key = "max_tokens" if self.config["provider"] == "local" else "max_completion_tokens"
        params: Dict[str, Any] = {"model": self.config["model"], "messages": messages, limit_key: limit}
        if not _NO_TEMPERATURE.search(self.config["model"]):
            params["temperature"] = temperature
        if schema:
            params["response_format"] = {"type": "json_schema", "json_schema": schema}
        if tools:
            params["tools"] = tools
        return params

    def _send(self, params: Dict[str, Any]):
        """İstek gönderir. Şema/temperature desteklenmezse parametreyi çıkarıp yeniden dener."""
        for _ in range(3):
            try:
                return self.client.chat.completions.create(**params)
            except BadRequestError as exc:
                detail = str(exc).lower()
                if "response_format" in params and ("response_format" in detail or "json_schema" in detail or "structured" in detail):
                    params.pop("response_format")
                    continue
                if "temperature" in params and "temperature" in detail:
                    params.pop("temperature")
                    continue
                raise AIServiceError(_describe_error(exc)) from exc
            except APIStatusError as exc:
                raise AIServiceError(_describe_error(exc)) from exc
            except Exception as exc:
                raise AIServiceError(f"AI servisine ulaşılamadı: {exc}") from exc
        raise AIServiceError("AI isteği parametre uyuşmazlığı nedeniyle tamamlanamadı")

    def _complete(self, messages: List[Dict[str, str]], schema: Optional[Dict[str, Any]],
                  max_tokens: Optional[int] = None, temperature: float = 0.0) -> str:
        """Tek bir sohbet tamamlama; metin döndürür."""
        response = self._send(self._build_params(messages, schema, max_tokens, temperature))
        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        if choice.finish_reason == "length" and not content:
            raise AIServiceError(
                "Model çıktı limitine takıldı ve cevap üretemedi. Ayarlar'dan 'Çıktı belirteç limiti'ni artırın "
                "veya düşünen olmayan bir model seçin."
            )
        return content

    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], max_tokens: Optional[int] = None):
        """Araç çağırmalı tek tur; model mesajını (content, tool_calls) döndürür."""
        response = self._send(self._build_params(messages, None, max_tokens, 0.0, tools))
        choice = response.choices[0]
        if choice.finish_reason == "length" and not choice.message.content and not choice.message.tool_calls:
            raise AIServiceError("Model çıktı limitine takıldı. Ayarlar'dan çıktı belirteç limitini artırın.")
        return choice.message

    # --- SQL üretimi ------------------------------------------------------
    def generate_sql(
        self,
        question: str,
        schema: DatabaseSchema,
        examples: Optional[List[SavedQuery]] = None,
        previous_sql: Optional[str] = None,
        previous_error: Optional[str] = None,
        dialect: Optional[Dialect] = None,
        context: str = "",
        turns: Optional[list] = None,
    ) -> Dict[str, str]:
        """Soruyu SQL'e çevirir. previous_* verilirse hata düzeltme turudur."""
        dialect = dialect or get_dialect("mssql")
        system = (SYSTEM_PROMPT.replace("{sql_name}", dialect.sql_name).replace("{quoting}", dialect.quoting)
                  .replace("{schema}", format_schema(schema)))
        if context:
            system += "\nBusiness knowledge:\n" + context + "\n"
        messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
        for example in examples or []:
            messages.append({"role": "user", "content": example.question})
            messages.append({"role": "assistant", "content": json.dumps(
                {"sql": example.sql_query, "explanation": ""}, ensure_ascii=False)})
        for turn in turns or []:
            messages.append({"role": "user", "content": turn.question})
            summary = f"[result: {turn.row_count} rows; columns: {', '.join(turn.columns[:12])}]" if turn.columns else ""
            messages.append({"role": "assistant", "content": json.dumps(
                {"sql": turn.sql, "explanation": turn.explanation}, ensure_ascii=False) + ("\n" + summary if summary else "")})
        messages.append({"role": "user", "content": question})
        if previous_sql and previous_error:
            messages.append({"role": "assistant", "content": json.dumps(
                {"sql": previous_sql, "explanation": ""}, ensure_ascii=False)})
            messages.append({"role": "user", "content": (
                f"That query failed with this error:\n{previous_error}\n"
                "Fix it. Return the corrected single SELECT query as JSON."
            )})

        started = time.time()
        raw = self._complete(messages, SQL_SCHEMA)
        parsed = parse_sql_answer(raw)
        logger.info("SQL üretildi (%.1fs, model=%s)", time.time() - started, self.model)
        return parsed

    def plan_job(self, text: str, now_iso: str) -> Dict[str, Any]:
        """Doğal dil isteğinden zamanlanmış rapor/uyarı planı çıkarır."""
        prompt = (
            "Turn the user's request into a scheduled job. Respond as JSON.\n"
            "- kind: 'report' (send results on a schedule) or 'alert' (notify only when a condition holds).\n"
            "- question: a standalone question for a database analyst. For an alert it must ask for ONE metric value "
            "(e.g. 'iade oranı yüzdesi'), so the condition can compare the first numeric value.\n"
            "- schedule: interval (minutes), daily (time HH:MM), weekly (day 0=Monday..6=Sunday, time) or monthly "
            "(day 1-28, time). Alerts without a stated schedule: daily 09:00. Reports without a time: 09:00.\n"
            "- condition: for alerts, op and value (null for rows>0 / rows==0); null for reports.\n"
            "- channels: include 'telegram' if the user mentions Telegram, 'webhook' if a webhook; otherwise ['inbox'].\n"
            "- name: short Turkish title.\n"
            f"Current local time: {now_iso}\n\nRequest: {text}"
        )
        raw = self._complete([{"role": "user", "content": prompt}], JOB_SCHEMA)
        data = _extract_json(raw)
        if not data:
            raise AIServiceError("İstek anlaşılamadı")
        return data

    def suggest_questions(self, catalog: str, count: int = 6) -> List[str]:
        """Şemaya bakıp bir iş kullanıcısının sorabileceği hazır soruları üretir."""
        prompt = (
            f"Below is a database catalog. Write {count} useful business questions a non-technical user could ask about this "
            "data, in Turkish, each under 12 words, answerable with SQL (counts, totals, top-N, trends, comparisons). "
            'Vary them. Respond as JSON: {"questions": ["..."]}.\n\n' + catalog
        )
        raw = self._complete([{"role": "user", "content": prompt}], SUGGEST_SCHEMA, temperature=0.5)
        data = _extract_json(raw) or {}
        questions = data.get("questions") if isinstance(data.get("questions"), list) else []
        return [str(q).strip() for q in questions if str(q).strip()][:count]

    def pick_tables(self, question: str, catalog: List[str], limit: int = 6) -> List[str]:
        """Çok tablolu şemalarda soruyla ilgili tabloları seçer (yalnızca adlar ve notlar gider)."""
        prompt = (
            f"Pick the tables most likely needed to answer the question (at most {limit}). "
            'Respond as JSON: {"tables": ["name", ...]} using the exact names from the catalog.\n\n'
            f"Question: {question}\n\nCatalog:\n" + "\n".join(catalog)
        )
        raw = self._complete([{"role": "user", "content": prompt}], TABLES_SCHEMA)
        data = _extract_json(raw) or {}
        tables = data.get("tables") if isinstance(data.get("tables"), list) else []
        return [str(t) for t in tables][:limit]

    # --- sonuç yorumu (isteğe bağlı) -------------------------------------
    def analyze_results(self, question: str, sql: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
        """İlk 20 satırı özetletir. Çağıran yalnızca kullanıcı açıkça izin verdiyse çağırır."""
        if not self.client or not rows:
            return None
        sample = [{k: _clip(v) for k, v in row.items()} for row in rows[:20]]
        prompt = (
            "Kullanıcının sorusu, çalıştırılan SQL ve sonuçların ilk satırları aşağıda. "
            "Türkçe yaz. 'summary': sonucu 1-2 cümleyle özetle. 'analysis': sayıları kullanarak "
            "2-4 cümlelik kısa yorum yap; veride olmayan şeyi uydurma.\n\n"
            f"Soru: {question}\nSQL: {sql}\nSatır sayısı: {len(rows)}\n"
            f"Örnek satırlar (JSON): {json.dumps(sample, ensure_ascii=False, default=str)}"
        )
        try:
            raw = self._complete([{"role": "user", "content": prompt}], ANALYSIS_SCHEMA, temperature=0.3)
            data = _extract_json(raw) or {}
            if data.get("summary") or data.get("analysis"):
                return {"summary": str(data.get("summary", "")), "analysis": str(data.get("analysis", ""))}
            return {"summary": raw[:400], "analysis": ""}
        except Exception as exc:
            logger.warning("Sonuç analizi alınamadı: %s", exc)
            return None

    def transcribe(self, audio: bytes, fmt: str = "wav", language: str = "tr") -> str:
        """Konuşmayı metne çevirir. OpenAI: ses-metin ucu; OpenRouter: ses girdili model (ayarlardaki stt_model)."""
        if not self.client:
            raise AIUnavailableError("Sesli sorgu için API anahtarı ayarlanmamış. Ayarlar sayfasından ekleyin.")
        provider = self.config["provider"]
        if provider == "local":
            raise AIServiceError("Sesli sorgu için OpenRouter veya OpenAI sağlayıcısı gerekli (yerel modelde ses desteği yok).")
        if provider == "openai":
            try:
                result = self.client.audio.transcriptions.create(
                    model="gpt-4o-mini-transcribe", file=(f"ses.{fmt}", audio), language=language)
            except Exception as exc:
                raise AIServiceError(f"Ses metne çevrilemedi: {exc}"[:300]) from exc
            return (result.text or "").strip()

        import base64
        model = Settings.load().get("stt_model") or "google/gemini-3.5-flash-lite"
        content = [
            {"type": "text", "text": "Transcribe this audio verbatim in its original language. "
                                     "Output only the transcript, no quotes, no commentary."},
            {"type": "input_audio", "input_audio": {"data": base64.b64encode(audio).decode("ascii"), "format": fmt}},
        ]
        params = {"model": model, "messages": [{"role": "user", "content": content}], "max_completion_tokens": 800}
        response = self._send(params)
        return (response.choices[0].message.content or "").strip()

    def ping(self) -> Dict[str, Any]:
        """Bağlantı testi: küçük bir istek atar, süreyi ve model adını döndürür."""
        started = time.time()
        self._complete([{"role": "user", "content": "Reply with the single word: ok"}], None, max_tokens=64)
        return {"ok": True, "latency_ms": int((time.time() - started) * 1000),
                "provider": self.config["provider"], "model": self.config["model"]}


class AIServiceError(Exception):
    """Kullanıcıya gösterilebilir AI hatası."""


def _describe_error(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    if status == 401:
        return "API anahtarı geçersiz veya yetkisiz (401). Ayarlar'dan anahtarı kontrol edin."
    if status == 402:
        return "Hesapta kredi/bakiye yetersiz (402)."
    if status == 404:
        return "Model bulunamadı (404). Ayarlar'dan model adını kontrol edin."
    if status == 429:
        return "İstek limiti aşıldı (429). Biraz sonra tekrar deneyin."
    body = getattr(exc, "message", None) or str(exc)
    return f"AI hatası ({status}): {body}"[:300]


def _clip(value: Any, limit: int = 80) -> Any:
    text = value if isinstance(value, (int, float, bool)) or value is None else str(value)
    return text[:limit] if isinstance(text, str) else text


def format_schema(schema: DatabaseSchema) -> str:
    """Şemayı modele kompakt, tip ve ilişki bilgisiyle verir."""
    lines: List[str] = []
    for table in schema.tables.values():
        columns = ", ".join(
            f"{c.name} {c.data_type}" + (" PK" if c.name in table.primary_key else "") for c in table.columns
        )
        lines.append(f"TABLE {table.name} ({columns})")
        for column, target, target_column in table.foreign_keys:
            lines.append(f"  FK {table.name}.{column} -> {target}.{target_column}")
    return "\n".join(lines)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Metinden ilk JSON nesnesini çıkarır (kod çiti veya ek açıklama olsa da)."""
    text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except ValueError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except ValueError:
            return None
    return None


def parse_sql_answer(raw: str) -> Dict[str, str]:
    """Model cevabından {sql, explanation} çıkarır: JSON, kod çiti ya da çıplak SQL."""
    data = _extract_json(raw)
    if data and isinstance(data.get("sql"), str) and data["sql"].strip():
        parsed = {"sql": data["sql"].strip(), "explanation": str(data.get("explanation", "")).strip()}
        follow_ups = [str(q).strip() for q in data.get("follow_ups", []) if str(q).strip()][:3]
        if follow_ups:
            parsed["follow_ups"] = follow_ups
        return parsed
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    sql = (fenced.group(1) if fenced else raw).strip()
    if not sql:
        raise AIServiceError("Model boş cevap döndürdü")
    return {"sql": sql, "explanation": ""}
