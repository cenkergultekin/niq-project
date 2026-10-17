"""Ordered desktop setup: optional identity, names-only consent, selected access."""
from flask import Blueprint, current_app, jsonify, request, session

from backend.config.config import Settings
from backend.core.approval import ApprovalRequired, require_approval
from backend.core.utils import ResponseFormatter, ValidationError
from backend.db.catalog import collect_catalog

onboarding_bp = Blueprint("onboarding", __name__, url_prefix="/api/onboarding")
ok = ResponseFormatter.format_success_response


@onboarding_bp.errorhandler(ValidationError)
def invalid(error):
    return jsonify(ResponseFormatter.format_error_response(str(error))), 400


@onboarding_bp.errorhandler(ApprovalRequired)
def approval(error):
    return jsonify({"success": False, "needs_approval": True, "summary": error.summary, "action": error.action}), 409


@onboarding_bp.get("")
def status():
    return jsonify(ok({"required": bool(current_app.config.get("ONBOARDING_REQUIRED") and not session.get("ONBOARDING_DONE"))}))


def require_account():
    account = Settings.load()["ai_account_id"]
    if not account or session.get("AI_VERIFIED") != account:
        raise ValidationError("Devam etmek için ChatGPT ile giriş yap veya API anahtarını doğrula.")


@onboarding_bp.post("/account")
def verify_account():
    from backend.services.ai_accounts import AccountStore
    from backend.services.ai_service import AIService, AIServiceError, AIUnavailableError
    account = Settings.load()["ai_account_id"]
    if not account:
        raise ValidationError("Önce bir hesap bağla.")
    if session.get("AI_VERIFIED") == account:
        return jsonify(ok({"verified": True}))
    config = AccountStore().configure(account)
    try:
        answer = AIService(config=config)._complete([{"role": "user", "content": "Reply with exactly: ok"}], None, max_tokens=512)
        if not answer.strip():
            raise AIServiceError("No completed text")
    except (AIServiceError, AIUnavailableError):
        raise ValidationError("Hesap doğrulanamadı. Girişini, kullanım iznini ve bakiyeyi kontrol et.") from None
    session["AI_VERIFIED"] = account
    return jsonify(ok({"verified": True}))


@onboarding_bp.post("/discover")
def discover():
    from backend.routes.routes import state
    data = request.get_json(silent=True) or {}
    require_account()
    require_approval(data, "discover_database_catalog")
    result = collect_catalog(state.profiles, state.connection_grants)
    session["DB_NAMES_APPROVED"] = True
    session["ONBOARDING_DATABASES"] = [item["id"] for item in result["databases"]]
    return jsonify(ok(result))


@onboarding_bp.post("/connect")
def connect():
    from backend.routes.routes import state
    data = request.get_json(silent=True) or {}
    require_account()
    if not session.get("DB_NAMES_APPROVED"):
        raise ValidationError("Önce yalnızca veritabanı adlarını bulmak için izin ver.")
    tokens = data.get("databases")
    if not isinstance(tokens, list) or not tokens or not set(tokens) <= set(session.get("ONBOARDING_DATABASES", [])):
        raise ValidationError("İzinle bulunan veritabanlarından en az birini seç.")
    require_approval(data, "access_selected_databases")
    state.connect_scope(tokens)
    session["ONBOARDING_DONE"] = True
    Settings.save({"onboarding_complete": True})
    return jsonify(ok(state.public_profile()))
