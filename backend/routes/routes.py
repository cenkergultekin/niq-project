"""
HTTP API: bağlantı, tablolar, doğal dil sorgusu, geçmiş, ayarlar ve sağlık.
"""
import logging
from dataclasses import asdict
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request, session
from werkzeug.exceptions import HTTPException

from backend.config.config import PROVIDERS, Config, Settings
from backend.core.approval import ApprovalRequired, require_approval
from backend.core.utils import ResponseFormatter, StringUtils, ValidationError
from backend.db import diagnostics
from backend.db.connector import Connector, friendly_error
from backend.db.dialects import DIALECTS, driver_available
from backend.db.profiles import (
    Profile,
    ProfileStore,
    delete_password,
    get_password,
    parse_connection_input,
    set_password,
)
from backend.services import models_catalog
from backend.services.ai_service import AIService, AIServiceError, AIUnavailableError
from backend.services.history import HistoryStore
from backend.services.query_service import run_question, suggest_tables

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api")

fail = ResponseFormatter.format_error_response
ok = ResponseFormatter.format_success_response


class AppState:
    """Uygulama genelinde paylaşılan servisler (tek kullanıcılı masaüstü uygulaması)."""

    def __init__(self):
        self.connector: Optional[Connector] = None
        self.ai_service = AIService()
        self.profiles = ProfileStore()
        self._history: Optional[HistoryStore] = None

    @property
    def history(self) -> HistoryStore:
        if self._history is None:
            self._history = HistoryStore()
        return self._history

    def connect(self, profile: Profile, password: Optional[str]) -> None:
        """Bağlanır. Kayıtsız (ad-hoc) bağlantının parolası oturum süresince keyring'de tutulur."""
        probe = Connector(profile, password=password)
        ok_ = probe.test_connection()
        probe.close()
        if not ok_:
            raise ValidationError("Bağlantı kurulamadı. Ayrıntı için 'Test' adımını çalıştırın.")
        if password:
            set_password(profile, password)
        if self.connector:
            self.connector.close()
        self.connector = Connector(profile)
        session["PROFILE"] = asdict(profile)
        session.permanent = True

    def manager(self) -> Connector:
        if not self.connector:
            data = session.get("PROFILE")
            if not data:
                raise ValidationError("Veritabanı bağlantısı yok. Önce bağlanın.")
            self.connector = Connector(Profile.from_dict(data))
        return self.connector

    def disconnect(self) -> None:
        data = session.pop("PROFILE", None)
        profile = self.connector.profile if self.connector else (Profile.from_dict(data) if data else None)
        if self.connector:
            self.connector.close()
            self.connector = None
        if profile and not self.profiles.get(profile.id):
            delete_password(profile)  # kayıtsız (ad-hoc) bağlantının parolasını saklama


state = AppState()
db_routes = state  # geriye dönük ad


def _body() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Geçersiz veya boş istek gövdesi")
    return data


@api_bp.errorhandler(ValidationError)
def _validation_error(exc: ValidationError) -> Tuple[Any, int]:
    return jsonify(fail(str(exc))), 400


@api_bp.errorhandler(ApprovalRequired)
def _approval_required(exc: ApprovalRequired) -> Tuple[Any, int]:
    body = fail(exc.summary, "APPROVAL_REQUIRED")
    body.update(needs_approval=True, action=exc.action, summary=exc.summary)
    return jsonify(body), 409


@api_bp.errorhandler(Exception)
def _unexpected_error(exc: Exception) -> Tuple[Any, int]:
    if isinstance(exc, HTTPException):
        return jsonify(fail(exc.description or exc.name)), exc.code or 500
    logger.exception("Beklenmeyen hata: %s", exc)
    return jsonify(fail("Beklenmeyen bir hata oluştu. Ayrıntılar log dosyasında.")), 500


# --- veritabanı türleri, keşif, teşhis (onaysız) ----------------------------------
def _profile_from_body(data: Dict[str, Any]) -> Tuple[Profile, str]:
    """İstekten profil ve parola çıkarır. 'connection_input' (URL/ODBC/yol) alanları doldurur."""
    merged = dict(data)
    password = merged.get("password") or merged.get("pwd") or ""
    raw = merged.get("connection_input") or merged.get("db_conn_str")
    if raw:
        parsed = parse_connection_input(StringUtils.sanitize_input(raw, 2000))
        if not parsed.get("recognized"):
            raise ValidationError("Bağlantı metni tanınmadı. URL, ODBC dizesi veya dosya yolu girin.")
        password = password or parsed.pop("password", "")
        parsed.pop("recognized", None)
        for key, value in parsed.items():
            if value not in (None, "") and not merged.get(key):
                merged[key] = value
    merged["user"] = merged.get("user") or merged.get("uid") or merged.get("username") or ""
    if merged.get("driver"):
        merged["options"] = {**(merged.get("options") or {}), "driver": merged["driver"]}
    merged["type"] = merged.get("type") or "mssql"
    try:
        profile = Profile.from_dict(merged)
    except (ValueError, TypeError) as exc:
        raise ValidationError(str(exc)) from exc
    return profile, password


@api_bp.route("/db/types", methods=["GET"])
def db_types():
    return jsonify(ok([{**d.public(), "driver_available": driver_available(d), "install_hint": d.install_hint}
                       for d in DIALECTS.values()]))


@api_bp.route("/db/discover", methods=["GET"])
def db_discover():
    return jsonify(ok(diagnostics.discover()))


@api_bp.route("/db/parse", methods=["POST"])
def db_parse():
    """Yapıştırılan bağlantı metninden alanları tahmin eder (parola yanıtta dönmez)."""
    parsed = parse_connection_input(_body().get("input", ""))
    parsed["has_password"] = bool(parsed.pop("password", ""))
    return jsonify(ok(parsed))


@api_bp.route("/db/diagnose", methods=["POST"])
def db_diagnose():
    """Bağlantıyı adım adım sınar; hiçbir şey kaydetmez."""
    data = _body()
    profile, password = _profile_from_body(data)
    if not password and data.get("profile_id"):
        saved = state.profiles.get(data["profile_id"])
        password = (get_password(saved) if saved else "") or ""
    return jsonify(ok(diagnostics.diagnose(profile, password)))


@api_bp.route("/db/databases", methods=["POST"])
def db_databases():
    """Sunucudaki veritabanlarını listeler (kullanıcı veritabanı adını bilmiyorsa)."""
    profile, password = _profile_from_body(_body())
    connector = Connector(profile, password=password)
    try:
        return jsonify(ok(connector.list_databases()))
    except Exception as exc:
        message = friendly_error(exc)
        return jsonify(fail(message + " " + diagnostics.explain_error(message))), 400
    finally:
        connector.close()


# --- bağlantı ---------------------------------------------------------------------
@api_bp.route("/set_db", methods=["POST"])
def set_database():
    """Kayıtsız (ad-hoc) bağlanır. Kayıt için /connections kullanılır."""
    profile, password = _profile_from_body(_body())
    if not password and not DIALECTS[profile.type].file_based:
        raise ValidationError("Parola gerekli")
    logger.info("Bağlanılıyor: %s %s@%s/%s", profile.type, profile.user, profile.host, profile.database)
    state.connect(profile, password)
    return jsonify(ok(profile.public(), "Veritabanı bağlantısı başarıyla kuruldu."))


@api_bp.route("/clear_db", methods=["POST"])
def clear_database_credentials():
    state.disconnect()
    return jsonify(ok(None, "Bağlantı kapatıldı."))


@api_bp.route("/connections", methods=["GET"])
def list_connections():
    return jsonify(ok([p.public() for p in state.profiles.list()]))


@api_bp.route("/connections", methods=["POST"])
def save_connection():
    """KRİTİK: parola dahil profili bu bilgisayara kaydeder (onay gerekir)."""
    data = _body()
    profile, password = _profile_from_body(data)
    require_approval(data, "save_connection", f"{profile.name} ({profile.user or 'kullanıcısız'})")
    existing = state.profiles.get(profile.name)
    if existing:
        profile.id = existing.id
    if password:
        set_password(profile, password)
    state.profiles.save(profile)
    return jsonify(ok(profile.public(), "Bağlantı profili kaydedildi."))


@api_bp.route("/connections/<ref>", methods=["DELETE"])
def delete_connection(ref: str):
    """KRİTİK: profili ve kayıtlı parolasını siler (onay gerekir)."""
    profile = state.profiles.get(ref)
    if not profile:
        return jsonify(fail("Profil bulunamadı.")), 404
    require_approval(request.get_json(silent=True) or {}, "delete_connection", profile.name)
    state.profiles.delete(ref)
    return jsonify(ok(None, "Profil silindi."))


@api_bp.route("/connections/<ref>/connect", methods=["POST"])
def connect_saved(ref: str):
    """Kayıtlı profile bağlanır (parola keyring'den)."""
    profile = state.profiles.get(ref)
    if not profile:
        return jsonify(fail("Profil bulunamadı.")), 404
    state.connect(profile, None)
    return jsonify(ok(profile.public(), "Bağlandı."))


@api_bp.route("/tables", methods=["GET"])
def get_tables():
    return jsonify(ok(state.manager().get_tables(refresh=request.args.get("refresh") == "1")))


@api_bp.route("/columns", methods=["POST"])
def get_columns():
    tables = _body().get("tables", [])
    if not tables:
        raise ValidationError("Tablo seçilmedi")
    if len(tables) > Config.MAX_TABLES_PER_QUERY:
        raise ValidationError(f"En fazla {Config.MAX_TABLES_PER_QUERY} tablo seçilebilir")
    tables = [StringUtils.sanitize_input(t) for t in tables]
    return jsonify(ok(state.manager().get_multiple_table_columns(tables)))


@api_bp.route("/suggest_tables", methods=["POST"])
def suggest():
    """Soru yazılırken ilgili tabloları tahmin eder."""
    question = StringUtils.sanitize_input(_body().get("question", ""), Config.MAX_QUERY_LENGTH)
    return jsonify(ok(suggest_tables(state.manager(), state.history, question)))


# --- sorgu --------------------------------------------------------------------
@api_bp.route("/query", methods=["POST"])
def execute_query():
    """Soru -> SQL -> doğrula -> çalıştır; hata olursa hatayı modele geri verip yeniden dener."""
    data = _body()
    question = StringUtils.sanitize_input(data.get("question", ""), Config.MAX_QUERY_LENGTH)
    tables = [StringUtils.sanitize_input(t) for t in data.get("tables", [])]
    try:
        payload, status = run_question(state.manager(), state.ai_service, state.history, question, tables)
    except AIUnavailableError as exc:
        return jsonify(fail(str(exc), "AI_UNAVAILABLE")), 503
    except AIServiceError as exc:
        return jsonify(fail(str(exc), "AI_ERROR")), 502
    return jsonify(payload), status


# --- geçmiş -------------------------------------------------------------------
@api_bp.route("/queries", methods=["GET"])
def get_saved_queries():
    limit = min(max(request.args.get("limit", 50, type=int), 1), 100)
    offset = max(request.args.get("offset", 0, type=int), 0)
    queries = state.history.list(state.manager().db_key, limit, offset)
    return jsonify(ok([q.to_dict() for q in queries]))


@api_bp.route("/queries/<int:query_id>", methods=["GET"])
def get_saved_query(query_id: int):
    query = state.history.get(state.manager().db_key, query_id)
    if not query:
        return jsonify(fail(f"Sorgu #{query_id} bulunamadı.")), 404
    return jsonify(ok(query.to_dict()))


@api_bp.route("/queries/<int:query_id>", methods=["DELETE"])
def delete_saved_query(query_id: int):
    if not state.history.delete(state.manager().db_key, query_id):
        return jsonify(fail(f"Sorgu #{query_id} bulunamadı.")), 404
    return jsonify(ok(None, f"Sorgu #{query_id} başarıyla silindi."))


# --- ayarlar ------------------------------------------------------------------
def _settings_payload() -> Dict[str, Any]:
    settings = Settings.load()
    provider = settings["provider"]
    return {
        "provider": provider,
        "providers": {name: {"label": p["label"], "has_key": Settings.has_api_key(name)}
                      for name, p in PROVIDERS.items()},
        "models": settings["models"],
        "catalog": {name: models_catalog.list_models(name) for name in PROVIDERS},
        "local_base_url": settings["local_base_url"],
        "max_output_tokens": settings["max_output_tokens"],
        "row_limit": settings["row_limit"],
        "ai_analysis": settings["ai_analysis"],
        "trust_server_cert": settings["trust_server_cert"],
    }


@api_bp.route("/settings", methods=["GET"])
def get_settings():
    return jsonify(ok(_settings_payload()))


@api_bp.route("/settings", methods=["POST"])
def update_settings():
    data = _body()
    updates: Dict[str, Any] = {}

    provider = data.get("provider")
    if provider is not None:
        if provider not in PROVIDERS:
            raise ValidationError("Bilinmeyen sağlayıcı")
        updates["provider"] = provider

    models = data.get("models")
    if isinstance(models, dict):
        clean = {k: str(v).strip()[:120] for k, v in models.items() if k in PROVIDERS and str(v).strip()}
        updates["models"] = clean

    for key, low, high in (("max_output_tokens", 256, 16000), ("row_limit", 1, 10000)):
        if key in data:
            try:
                value = int(data[key])
            except (TypeError, ValueError):
                raise ValidationError(f"{key} sayı olmalı") from None
            updates[key] = min(max(value, low), high)
    for key in ("ai_analysis", "trust_server_cert"):
        if key in data:
            updates[key] = bool(data[key])
    if updates.get("ai_analysis") and not Settings.load()["ai_analysis"]:
        require_approval(data, "enable_ai_analysis")
    if "local_base_url" in data:
        url = str(data["local_base_url"]).strip()
        if url and not url.startswith(("http://", "https://")):
            raise ValidationError("Yerel sunucu adresi http:// veya https:// ile başlamalı")
        updates["local_base_url"] = url

    key_provider = data.get("api_key_provider") or updates.get("provider") or Settings.load()["provider"]
    if data.get("clear_api_key"):
        require_approval(data, "clear_api_key", PROVIDERS[key_provider]["label"])
        Settings.delete_api_key(key_provider)
    elif data.get("api_key"):
        Settings.set_api_key(key_provider, str(data["api_key"]).strip())

    Settings.save(updates)
    state.ai_service.reconfigure()
    return jsonify(ok(_settings_payload(), "Ayarlar kaydedildi."))


@api_bp.route("/settings/test", methods=["POST"])
def test_ai():
    """Kayıtlı ayarlarla küçük bir istek atar."""
    try:
        return jsonify(ok(state.ai_service.ping()))
    except AIUnavailableError as exc:
        return jsonify(fail(str(exc), "AI_UNAVAILABLE")), 503
    except AIServiceError as exc:
        return jsonify(fail(str(exc), "AI_ERROR")), 502


# --- sağlık -------------------------------------------------------------------
@api_bp.route("/health", methods=["GET"])
def health_check():
    ai = state.ai_service
    return jsonify(ok({
        "status": "healthy",
        "database_connected": bool(state.connector or session.get("PROFILE")),
        "ai_service_available": ai.ai_available,
        "provider": ai.config.get("provider"),
        "model": ai.model,
        "message": "NIQ is running",
    }))
