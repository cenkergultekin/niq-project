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
        },
        "required": ["sql", "explanation"],
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
- Respond as JSON: {"sql": "...", "explanation": "..."}; the explanation is 1-2 sentences in Turkish.

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
    def _complete(self, messages: List[Dict[str, str]], schema: Optional[Dict[str, Any]],
                  max_tokens: Optional[int] = None, temperature: float = 0.0) -> str:
        """Tek bir sohbet tamamlama. Şema/temperature desteklenmezse sırayla geri çekilir."""
        if not self.client:
            raise AIUnavailableError("AI için API anahtarı ayarlanmamış. Ayarlar sayfasından ekleyin.")

        limit = max_tokens or self.config["max_output_tokens"]
        # Yerel sunucular eski max_tokens adını bekler; OpenAI/OpenRouter için doğru alan max_completion_tokens
        limit_key = "max_tokens" if self.config["provider"] == "local" else "max_completion_tokens"
        params: Dict[str, Any] = {"model": self.config["model"], "messages": messages, limit_key: limit}
        use_temperature = not _NO_TEMPERATURE.search(self.config["model"])
        if use_temperature:
            params["temperature"] = temperature
        if schema:
            params["response_format"] = {"type": "json_schema", "json_schema": schema}

        for _ in range(3):
            try:
                response = self.client.chat.completions.create(**params)
                break
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
        else:
            raise AIServiceError("AI isteği parametre uyuşmazlığı nedeniyle tamamlanamadı")

        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        if choice.finish_reason == "length" and not content:
            raise AIServiceError(
                "Model çıktı limitine takıldı ve cevap üretemedi. Ayarlar'dan 'Çıktı belirteç limiti'ni artırın "
                "veya düşünen olmayan bir model seçin."
            )
        return content

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
        return {"sql": data["sql"].strip(), "explanation": str(data.get("explanation", "")).strip()}
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    sql = (fenced.group(1) if fenced else raw).strip()
    if not sql:
        raise AIServiceError("Model boş cevap döndürdü")
    return {"sql": sql, "explanation": ""}
