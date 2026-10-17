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


@onboarding_bp.post("/discover")
def discover():
    from backend.routes.routes import state
    data = request.get_json(silent=True) or {}
    require_approval(data, "discover_database_catalog")
    result = collect_catalog(state.profiles, state.connection_grants)
    session["DB_NAMES_APPROVED"] = True
    session["ONBOARDING_DATABASES"] = [item["id"] for item in result["databases"]]
    return jsonify(ok(result))


@onboarding_bp.post("/connect")
def connect():
    from backend.routes.routes import state
    data = request.get_json(silent=True) or {}
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
