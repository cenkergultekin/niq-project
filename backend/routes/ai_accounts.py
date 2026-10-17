"""User-initiated account setup. MCP never receives credentials or login tools."""
import json
import sys
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request

from backend.config.config import Settings
from backend.core.approval import ApprovalRequired, require_approval
from backend.core.utils import ResponseFormatter, ValidationError
from backend.services.ai_accounts import ACCOUNT_PROVIDERS, AccountStore
from backend.services.ai_auth import browser_auth

accounts_bp = Blueprint("ai_accounts", __name__)
ok = ResponseFormatter.format_success_response


@accounts_bp.errorhandler(ValidationError)
def invalid(error):
    return jsonify(ResponseFormatter.format_error_response(str(error), "VALIDATION_ERROR")), 400


@accounts_bp.errorhandler(ApprovalRequired)
def approval(error):
    return jsonify({"success": False, "needs_approval": True, "action": error.action, "summary": error.summary}), 409


def public_account(item):
    return {k: item[k] for k in ("id", "provider", "auth", "label", "model", "base_url", "connected") if k in item}


def account_models(account_id):
    store = AccountStore()
    config = store.configure(account_id)
    provider = config["provider"]
    if provider in ("azure", "compatible", "local"):
        return [{"id": config["model"], "name": config["model"]}] if config["model"] else []
    url = config["base_url"] + "/models"
    headers = {"Authorization": "Bearer " + config["api_key"]}
    if provider == "anthropic":
        url = "https://api.anthropic.com/v1/models"
        headers = {"x-api-key": config["api_key"], "anthropic-version": "2023-06-01"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        if provider == "chatgpt":
            return [{"id": m["slug"], "name": m["display_name"]} for m in data["models"] if m.get("visibility") == "list"]
        return [{"id": m["id"], "name": m.get("display_name") or m.get("name") or m["id"]} for m in data["data"]]
    except (requests.RequestException, ValueError, KeyError):
        raise ValidationError("Hesabın modelleri alınamadı. Girişini kontrol ederek yeniden dene.") from None


def reconfigure():
    from backend.routes.routes import state
    from backend.services.ai_service import AIService
    # In-flight requests keep their original account and billing identity.
    state.ai_service = AIService()


@accounts_bp.get("/api/ai/accounts")
def list_accounts():
    settings = Settings.load()
    return jsonify(ok({"accounts": [public_account(a) for a in AccountStore().list()],
                       "providers": ACCOUNT_PROVIDERS, "active": settings["ai_account_id"],
                       "voice": settings["voice_account_id"]}))


@accounts_bp.post("/api/ai/accounts")
def add_account():
    data = request.get_json(silent=True) or {}
    require_approval(data, "save_ai_account")
    item = AccountStore().add_key(data)
    Settings.save({"ai_account_id": item["id"]})
    reconfigure()
    return jsonify(ok(public_account(item)))


@accounts_bp.get("/api/ai/accounts/<account_id>/models")
def models(account_id):
    return jsonify(ok(account_models(account_id)))


@accounts_bp.post("/api/ai/accounts/<account_id>/label")
def label_account(account_id):
    value = str((request.get_json(silent=True) or {}).get("label") or "").strip()[:80]
    if not value:
        raise ValidationError("Hesap adı gerekli.")
    store = AccountStore()
    return jsonify(ok(public_account(store.put({**store.get(account_id), "label": value}))))


@accounts_bp.post("/api/ai/select")
def select_account():
    data = request.get_json(silent=True) or {}
    account_id = str(data.get("account_id") or "")
    kind = data.get("kind", "chat")
    if kind not in ("chat", "voice"):
        raise ValidationError("Geçersiz hesap türü.")
    if account_id:
        store = AccountStore()
        item = store.get(account_id)
        if not item.get("connected"):
            raise ValidationError("Hesaba yeniden giriş yap.")
        if kind == "voice" and not ACCOUNT_PROVIDERS[item["provider"]]["voice"]:
            raise ValidationError("Ses için OpenAI API veya OpenRouter hesabı seç.")
        model = str(data.get("model") or item["model"]).strip()[:120]
        if kind == "chat":
            if not model:
                raise ValidationError("Model seç.")
            if item["provider"] == "chatgpt" and model not in {m["id"] for m in account_models(account_id)}:
                raise ValidationError("Model bu ChatGPT hesabında kullanılamıyor.")
            store.put({**item, "model": model})
    Settings.save({"voice_account_id" if kind == "voice" else "ai_account_id": account_id})
    reconfigure()
    welcome = (kind == "chat" and account_id and item["provider"] == "chatgpt"
               and not Settings.load()["chatgpt_welcome_seen"])
    return jsonify(ok({"account_id": account_id, "welcome_required": bool(welcome)}))


@accounts_bp.post("/api/ai/welcome/dismiss")
def dismiss_welcome():
    Settings.save({"chatgpt_welcome_seen": True})
    return jsonify(ok({}))


@accounts_bp.post("/api/ai/accounts/<account_id>/disconnect")
def disconnect(account_id):
    data = request.get_json(silent=True) or {}
    require_approval(data, "disconnect_ai_account")
    revoked = AccountStore().disconnect(account_id)
    settings = Settings.load()
    # Keep a disconnected selection: never silently fall back to billed legacy API.
    reconfigure()
    provider = AccountStore().get(account_id)["provider"]
    message = "NIQ'deki giriş bilgileri kaldırıldı. API anahtarı sağlayıcıda geçerli kalır."
    if provider == "chatgpt":
        message = ("Çıkış yapıldı." if revoked else
                   "Yerel oturum kapatıldı. Uzaktaki izin iptali doğrulanamadı; sağlayıcı ayarlarından kaldır.")
    return jsonify(ok({"revoked": revoked if provider == "chatgpt" else None, "active": settings["ai_account_id"]}, message))


@accounts_bp.post("/api/ai/auth/start")
def start_login():
    data = request.get_json(silent=True) or {}
    require_approval(data, "connect_ai_account")
    callback = request.host_url.rstrip("/") + "/auth/callback"
    return jsonify(ok(browser_auth.start(data.get("provider"), callback, data.get("account_id", ""))))


@accounts_bp.get("/api/ai/auth/status/<attempt>")
def login_status(attempt):
    return jsonify(ok(browser_auth.status(attempt)))


@accounts_bp.post("/api/ai/auth/cancel")
def cancel_login():
    browser_auth.cancel((request.get_json(silent=True) or {}).get("attempt", ""))
    return jsonify(ok({}))


@accounts_bp.get("/auth/callback")
def login_callback():
    try:
        browser_auth.finish(request.args)
        message = "Giriş tamamlandı. NIQ'ye dönebilirsin."
        code = 200
    except ValidationError:
        message, code = "Giriş tamamlanamadı. NIQ'ye dönerek yeniden dene.", 400
    # No provider descriptions, query parameters, identities, or tokens in HTML.
    return ("<!doctype html><html lang=tr><meta charset=utf-8><title>NIQ</title>"
            "<body style='background:#091222;color:#dce7f7;font:18px system-ui;padding:48px'>" + message + "</body></html>",
            code, {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                   "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"})


@accounts_bp.get("/api/mcp/config")
def mcp_config():
    return jsonify(ok({"config": mcp_configuration()}))


def mcp_configuration():
    if getattr(sys, "frozen", False):
        command, args = str(Path(sys.executable).with_name("NIQ-MCP.exe")), []
    else:
        command, args = sys.executable, [str(Path(__file__).resolve().parents[2] / "niq_mcp.py")]
    return json.dumps({"mcpServers": {"niq": {"command": command, "args": args}}}, indent=2)
