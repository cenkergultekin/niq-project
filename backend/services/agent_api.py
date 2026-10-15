"""
Agent'lar için ortak yüzey (MCP sunucusu ve testler kullanır).

Agent'a ham veritabanı erişimi vermek yerine NIQ'nun katmanlarını açar: salt-okunur SQL güvenliği, iş sözlüğü,
tablo notları, doğrulanmış örnekler ve denetim kaydı. Kimlik bilgisi alan veya kalıcı otomasyon kuran adımlar
(profil kaydetme, zamanlama, bildirim ayarı) bilerek yoktur: bunlar kullanıcı onayı gerektirir ve arayüz/CLI'dan yapılır.
"""
import logging
from typing import Any, Dict, List, Optional

from backend.config.config import Settings
from backend.core.utils import ValidationError
from backend.db.connector import Connector
from backend.db.profiles import Profile, ProfileStore
from backend.services.ai_service import AIService, AIServiceError, AIUnavailableError
from backend.services.history import HistoryStore
from backend.services.jobs import JobStore
from backend.services.knowledge import KnowledgeStore
from backend.services.query_service import run_question, run_sql

logger = logging.getLogger(__name__)

MAX_ROWS_TO_AGENT = 50


class AgentAPI:
    def __init__(self, profiles: Optional[ProfileStore] = None, history: Optional[HistoryStore] = None,
                 knowledge: Optional[KnowledgeStore] = None, jobs: Optional[JobStore] = None,
                 ai: Optional[AIService] = None):
        self.profiles = profiles or ProfileStore()
        self.history = history or HistoryStore()
        self.knowledge = knowledge or KnowledgeStore()
        self.jobs = jobs or JobStore()
        self._ai = ai

    @property
    def ai(self) -> AIService:
        if self._ai is None:
            self._ai = AIService()
        return self._ai

    def _profile(self, connection: str) -> Profile:
        profile = self.profiles.get(connection)
        if not profile:
            names = ", ".join(p.name for p in self.profiles.list()) or "(kayıtlı profil yok)"
            raise ValidationError(
                f"'{connection}' adında kayıtlı bağlantı yok. Kayıtlı bağlantılar: {names}. "
                "Yeni bağlantı kullanıcı tarafından NIQ arayüzünden veya `niq_cli.py connections save` ile eklenmelidir.")
        return profile

    @staticmethod
    def _trim(rows: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
        rows = rows or []
        return {"rows": rows[:MAX_ROWS_TO_AGENT], "row_count": len(rows), "shown": min(len(rows), MAX_ROWS_TO_AGENT),
                "truncated": len(rows) > MAX_ROWS_TO_AGENT}

    # --- keşif ---------------------------------------------------------------------------
    def list_connections(self) -> List[Dict[str, Any]]:
        return [{"name": p.name, "type": p.type, "host": p.host, "database": p.database, "user": p.user}
                for p in self.profiles.list()]

    def list_tables(self, connection: str) -> Dict[str, Any]:
        profile = self._profile(connection)
        connector = Connector(profile)
        try:
            notes = self.knowledge.get_notes(connector.db_key)
            return {"tables": [{"name": t, **({"note": notes[t]["note"]} if t in notes else {})}
                               for t in connector.get_tables(refresh=True)]}
        finally:
            connector.close()

    def describe_tables(self, connection: str, tables: List[str]) -> Dict[str, Any]:
        profile = self._profile(connection)
        connector = Connector(profile)
        try:
            known = set(connector.get_tables())
            missing = [t for t in tables if t not in known]
            if missing:
                raise ValidationError("Bulunamayan tablo(lar): " + ", ".join(missing))
            notes = self.knowledge.get_notes(connector.db_key)
            profile_values = self.knowledge.get_profile(connector.db_key, tables)
            described = {}
            for name in tables:
                info = connector.get_table_info(name)
                described[name] = {
                    "columns": [c.describe() for c in info.columns], "primary_key": info.primary_key,
                    "foreign_keys": [f"{c} -> {t}.{r}" for c, t, r in info.foreign_keys],
                    **({"note": notes[name]["note"]} if name in notes else {}),
                    **({"known_values": profile_values[name]} if name in profile_values else {}),
                }
            return {"dialect": connector.dialect.sql_name, "tables": described}
        finally:
            connector.close()

    # --- sorgu -----------------------------------------------------------------------------
    def run_sql(self, connection: str, sql: str) -> Dict[str, Any]:
        """Salt-okunur, doğrulanmış SELECT. Ham veritabanı bağlantısından farkı: yazma, çoklu ifade, sistem tabloları yok."""
        profile = self._profile(connection)
        connector = Connector(profile)
        try:
            result = run_sql(connector, sql)
        finally:
            connector.close()
        if not result.get("success"):
            return {"success": False, "error": result.get("error"), "sql": result.get("sql", sql)}
        return {"success": True, "sql": result["sql"], **self._trim(result["results"]), "source_truncated": result["truncated"]}

    def ask(self, connection: str, question: str, tables: Optional[List[str]] = None) -> Dict[str, Any]:
        """Doğal dil sorusu: tablo seçimi, iş sözlüğü, doğrulanmış örnekler ve hata düzeltmeyle SQL üretir ve çalıştırır."""
        profile = self._profile(connection)
        connector = Connector(profile)
        try:
            payload, _ = run_question(connector, self.ai, self.history, self.knowledge, question.strip(), tables or None)
        except (AIUnavailableError, AIServiceError) as exc:
            return {"success": False, "error": str(exc)}
        finally:
            connector.close()
        result = {"success": payload["success"], "sql": payload["sql"], "explanation": payload.get("explanation", ""),
                  "tables_used": payload.get("tables_used", []), "query_id": payload.get("query_id"),
                  "attempts": payload.get("attempts", 1)}
        if payload["success"]:
            result.update(self._trim(payload["results"]))
        else:
            result["error"] = payload.get("error")
        return result

    # --- bilgi katmanı -----------------------------------------------------------------------
    def get_glossary(self, connection: str) -> Dict[str, Any]:
        profile = self._profile(connection)
        key = Connector(profile).db_key
        return {"glossary": self.knowledge.list_glossary(key), "table_notes": self.knowledge.get_notes(key)}

    def add_glossary_term(self, connection: str, term: str, definition: str, sql_hint: str = "") -> Dict[str, Any]:
        """Kullanıcının verdiği bir iş tanımını kaydeder (sonraki sorularda modele verilir). Sırlar içermemelidir."""
        profile = self._profile(connection)
        key = Connector(profile).db_key
        try:
            term_id = self.knowledge.add_term(key, term, definition, sql_hint)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return {"success": True, "id": term_id}

    # --- raporlar ------------------------------------------------------------------------------
    def list_reports(self) -> List[Dict[str, Any]]:
        return [{"id": j["id"], "name": j["name"], "kind": j["kind"], "schedule": j["schedule_text"],
                 "condition": j["condition_text"], "enabled": j["enabled"], "last_run": j["last_run"],
                 "last_status": j["last_status"], "last_message": j["last_message"]} for j in self.jobs.list()]

    def recent_results(self, limit: int = 10) -> List[Dict[str, Any]]:
        runs = self.jobs.runs(limit=min(max(limit, 1), 30))
        return [{"job": r["job_name"], "ran_at": r["ran_at"], "status": r["status"], "message": r["message"],
                 "row_count": r["row_count"], "rows": (r["rows"] or [])[:10]} for r in runs]

    def status(self) -> Dict[str, Any]:
        config = Settings.ai_config()
        return {"connections": len(self.profiles.list()), "ai_provider": config["provider"], "ai_model": config["model"],
                "ai_ready": bool(config["api_key"]), "reports": len(self.jobs.list())}
