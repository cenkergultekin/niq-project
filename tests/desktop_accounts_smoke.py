"""Real Qt UI with a synthetic OAuth issuer, signed JWT, and isolated in-memory vault."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    home = Path(tempfile.mkdtemp(prefix="niq-accounts-ui-"))
    os.environ.update(NIQ_HOME=str(home), KEYRING_SERVICE="niq-test-" + home.name, OPENAI_API_KEY="", OPENROUTER_API_KEY="")
    import jwt
    import keyring
    from cryptography.hazmat.primitives.asymmetric import rsa
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    import main_desktop
    from backend.config.config import Settings
    from backend.db.demo import demo_database
    from backend.db.profiles import Profile
    from backend.routes import ai_accounts
    from backend.routes.routes import state
    from backend.services import ai_auth, models_catalog

    values, urls = {}, []
    keyring.get_password = lambda service, name: values.get(name)
    keyring.set_password = lambda service, name, value: values.__setitem__(name, value)
    keyring.delete_password = lambda service, name: values.pop(name, None)
    ai_auth.webbrowser.open = lambda url: urls.append(url) or True
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ai_auth.JWKS.get_signing_key_from_jwt = lambda token: NS(key=private.public_key())
    models_catalog.list_models = lambda provider: []
    ai_accounts.account_models = lambda account: [{"id": "synthetic-model", "name": "Synthetic model"}]

    def exchange(url, **kwargs):
        query = {k: v[0] for k, v in parse_qs(urlsplit(urls[-1]).query).items()}
        claims = {"iss": ai_auth.ISSUER, "aud": "oaiapp_synthetic", "sub": "synthetic", "exp": int(time.time()) + 600,
                  "nonce": query["nonce"]}
        return {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "scope": ai_auth.SCOPES,
                "expires_in": 3600, "id_token": jwt.encode(claims, private, algorithm="RS256")}
    ai_auth._post = exchange
    with main_desktop.flask_app.test_request_context():
        state.connect(Profile(type="sqlite", name="Örnek satışlar", database=str(demo_database())), None)
    qt = QApplication(sys.argv[:1])
    window = main_desktop.NIQDesktopApp()
    window.show()
    started, stage, pending = time.monotonic(), 0, False
    checks = []

    def finish(error=None):
        timer.stop()
        window.grab().save(str(home / "accounts.png"))
        print(json.dumps({"passed": error is None, "checks": checks, "error": error, "screenshots": str(home)}), flush=True)
        window.shutdown()
        qt.exit(1 if error else 0)

    def result(raw):
        nonlocal stage, pending
        pending = False
        try:
            data = json.loads(raw or "{}")
            if not data.get("ready"):
                return
            js, screenshot = "", None
            if stage == 0:
                js = "NIQ.go('settings')"
            elif stage == 1:
                if not data["loaded"]:
                    return
                js = "NIQ.$('ai-chatgpt').click()"
            elif stage == 2:
                if not data["approval"]:
                    return
                checks.append("explicit-login-approval")
                js = "NIQ.$('confirm-yes').click()"
            elif stage == 3:
                if not urls:
                    return
                query = parse_qs(urlsplit(urls[-1]).query)
                callback = "/auth/callback?" + urlencode({"state": query["state"][0], "code": "synthetic", "client_id": "oaiapp_synthetic"})
                js = "fetch(" + json.dumps(callback) + ")"
            elif stage == 4:
                if data["model"] != "synthetic-model":
                    return
                assert Settings.load()["ai_account_id"] == ""
                screenshot = "settings.png"
                checks.append("verified-oauth-model-picker-no-implicit-switch")
                js = "NIQ.$('ai-use').click()"
            elif stage == 5:
                if not data["welcome"]:
                    return
                assert Settings.load()["ai_account_id"]
                assert Settings.load()["voice_account_id"] == ""
                screenshot = "welcome.png"
                checks.append("plan-welcome-voice-separation")
                js = "NIQ.$('ai-welcome-dismiss').click()"
            elif stage == 6:
                if not Settings.load()["chatgpt_welcome_seen"]:
                    return
                js = "NIQ.$('ai-mcp-copy').click()"
            elif stage == 7:
                if not data["mcp"]:
                    return
                assert "mcpServers" in data["mcp"] and "synthetic-access" not in data["mcp"]
                if QApplication.clipboard().text() != data["mcp"]:
                    return
                checks.append("mcp-config-no-credentials")
                js = "NIQ.go('chat')"
            elif stage == 8:
                assert data["plan"] and data["barHeight"] == 48
                checks.append("plan-indicator-compact-composer")
                finish()
                return
            stage += 1
            if screenshot:
                pending = True
                def resume():
                    nonlocal pending
                    window.grab().save(str(home / screenshot))
                    window.web_view.page().runJavaScript(js)
                    pending = False
                QTimer.singleShot(600, resume)
            elif js:
                window.web_view.page().runJavaScript(js)
        except Exception as exc:
            finish(str(exc))

    def tick():
        nonlocal pending
        if time.monotonic() - started > 70:
            finish("Timeout at stage " + str(stage))
        elif not pending:
            pending = True
            window.web_view.page().runJavaScript("""JSON.stringify({
                ready:!!window.NIQ?.accounts,loaded:document.querySelectorAll('.provider-choice').length === 4,
                approval:!!document.getElementById('confirm')?.open,welcome:!!document.getElementById('ai-welcome')?.open,
                model:document.getElementById('ai-model')?.value,mcp:document.getElementById('ai-mcp-config')?.textContent,
                plan:!document.getElementById('ai-plan')?.hidden,barHeight:document.querySelector('.bar')?.getBoundingClientRect().height
            })""", result)
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(200)
    sys.exit(qt.exec())


if __name__ == "__main__":
    main()
