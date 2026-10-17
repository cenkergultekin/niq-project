"""
Flask uygulama fabrikası.
"""
import logging
import os
import sys
from datetime import timedelta
from urllib.parse import urlparse

from flask import Flask, abort, render_template, request, session

from backend.config.config import Config
from backend.core.utils import setup_logging
from backend.routes.ai_accounts import accounts_bp
from backend.routes.onboarding import onboarding_bp
from backend.routes.routes import api_bp

# PyInstaller paketinde şablon ve statik dosyalar _MEIPASS altındadır
BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}


def create_app() -> Flask:
    setup_logging()
    app = Flask(
        __name__,
        template_folder=os.path.join(BASE_DIR, "frontend", "templates"),
        static_folder=os.path.join(BASE_DIR, "frontend", "static"),
    )
    app.config.update(
        SECRET_KEY=Config.secret_key(),
        DEBUG=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SEND_FILE_MAX_AGE_DEFAULT=0,
        ONBOARDING_REQUIRED=False,
    )

    @app.before_request
    def local_only_guard():
        """DNS rebinding ve başka sitelerden gelen istekleri engeller: yalnızca yerel Host/Origin."""
        if urlparse("//" + request.host).hostname not in LOCAL_HOSTNAMES:
            abort(403)
        origin = request.headers.get("Origin")
        if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
            if urlparse(origin).netloc != request.host:
                abort(403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("Sec-Fetch-Site") == "cross-site":
            abort(403)
        if app.config["ONBOARDING_REQUIRED"] and not session.get("ONBOARDING_DONE"):
            allowed = ("/api/onboarding", "/api/ai/", "/api/settings", "/api/health", "/api/db/types", "/api/mcp/config")
            if request.path.startswith("/api/") and not request.path.startswith(allowed):
                return {"success": False, "error": "Devam etmek için veritabanı erişim izinlerini tamamla.",
                        "error_code": "ONBOARDING_REQUIRED"}, 423

    app.register_blueprint(api_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(onboarding_bp)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.errorhandler(404)
    def not_found(_error):
        return {"error": "Endpoint not found"}, 404

    @app.errorhandler(403)
    def forbidden(_error):
        return {"error": "Forbidden"}, 403

    @app.errorhandler(500)
    def internal_error(error):
        logging.getLogger(__name__).error("Internal server error: %s", error)
        return {"error": "Internal server error"}, 500

    return app


app = create_app()

if __name__ == "__main__":
    from backend.config.config import Settings
    from backend.services.jobs import start_background_services
    app.config["ONBOARDING_REQUIRED"] = not Settings.load()["onboarding_complete"]
    if not app.config["ONBOARDING_REQUIRED"]:
        start_background_services()
    app.run(debug=False, host="127.0.0.1", port=int(os.getenv("NIQ_PORT", "5000")))
