"""Real Qt first run: no login/discovery, names consent, selected access, zero content."""
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    home = Path(tempfile.mkdtemp(prefix="niq-first-run-"))
    os.environ.update(NIQ_HOME=str(home), KEYRING_SERVICE="niq-test-" + home.name, OPENAI_API_KEY="", OPENROUTER_API_KEY="")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    import main_desktop
    from backend.db import catalog
    from backend.db.connector import Connector
    from backend.db.profiles import Profile
    from backend.routes import ai_accounts, onboarding
    from backend.routes.routes import state
    from backend.services import ai_auth
    from backend.services.ai_service import AIService

    calls = []
    AIService.suggest_questions = lambda *args, **kwargs: ["Kaç sipariş var?"]
    import keyring
    vault = {}
    keyring.get_password = lambda service, name: vault.get(name)
    keyring.set_password = lambda service, name, value: vault.__setitem__(name, value)
    keyring.delete_password = lambda service, name: vault.pop(name, None)
    def valid_key(data):
        calls.append("account-verified")
        return {**data, "model": "gpt-6-luna"}
    ai_accounts.validate_key_account = valid_key
    ai_accounts.provider_models = lambda config: [{"id": "gpt-6-luna", "name": "Luna"}, {"id": "gpt-4o-mini", "name": "Mini"}]
    original = onboarding.collect_catalog
    def collect(*args):
        calls.append("names")
        return original(*args)
    onboarding.collect_catalog = collect
    catalog.diagnostics.discover = lambda *args: {"local_servers": []}
    ai_auth.webbrowser.open = lambda *args: calls.append("unexpected-login") or False
    def forbidden(*args, **kwargs):
        calls.append("unexpected-content")
        raise AssertionError("No content reads during onboarding")
    Connector.execute_readonly = forbidden
    for name in ("sales", "crm"):
        path = home / (name + ".sqlite")
        with sqlite3.connect(path) as conn:
            conn.executescript("CREATE TABLE orders(id INTEGER); INSERT INTO orders VALUES(1);")
        state.profiles.save(Profile(type="sqlite", name=name, database=str(path)))
    main_desktop.flask_app.config["ONBOARDING_REQUIRED"] = True
    qt = QApplication(sys.argv[:1])
    window = main_desktop.NIQDesktopApp()
    window.show()
    started, stage, pending = time.monotonic(), 0, False
    checks = []
    plan_checked = False

    def finish(error=None):
        timer.stop()
        print(json.dumps({"passed": error is None, "checks": checks, "calls": calls, "error": error, "screenshots": str(home)}), flush=True)
        window.shutdown()
        qt.exit(1 if error else 0)

    def result(raw):
        nonlocal stage, pending, plan_checked
        pending = False
        try:
            data = json.loads(raw or "{}")
            if not data.get("ready"):
                return
            js = ""
            if stage == 0:
                if not data["onboarding"] or not data["account"]:
                    return
                assert calls == [] and state.connector is None
                assert data["noContinue"]
                assert data["noRetry"] and data["turkish"] and data["noProviderHeading"]
                if not plan_checked:
                    plan_checked = True
                    window.web_view.page().runJavaScript("NIQ.$('ai-upgrade').click()")
                    return
                assert data["plan"]
                checks.append("turkish-options-no-retry-plan-without-login")
                window.web_view.page().runJavaScript("NIQ.$('niq-plan-close').click()")
                window.grab().save(str(home / "login.png"))
                checks.append("login-options-no-browser-no-discovery")
                js = "NIQ.$('ai-api-toggle').click()"
            elif stage == 1:
                if not data["keyForm"]:
                    return
                assert data["centered"] and data["wideButton"] and data["noContinue"], str(data)
                window.grab().save(str(home / "api-form.png"))
                checks.append("centered-wide-single-connect-action")
                js = "NIQ.$('ai-new-label').value='Synthetic'; NIQ.$('ai-new-key').value='synthetic-key'; NIQ.$('ai-add').click()"
            elif stage == 2:
                if not data["approval"]:
                    return
                js = "NIQ.$('confirm-yes').click()"
            elif stage == 3:
                if not data["permission"]:
                    return
                assert calls == ["account-verified"] and not data["dock"]
                checks.append("three-field-key-verified-auto-advances")
                checks.append("names-consent-required")
                js = "NIQ.$('onboarding-discover').click()"
            elif stage == 4:
                if data["databases"] != 2:
                    return
                assert calls == ["account-verified", "names"] and state.connector is None
                checks.append("names-only-no-connection")
                js = "const xs=[...document.querySelectorAll('.db-catalog-item input')]; xs[1].click(); NIQ.$('db-scope-connect-btn').click()"
            elif stage == 5:
                if data["onboarding"]:
                    return
                assert state.connector is not None and len(state.workspace) == 1 and calls == ["account-verified", "names"]
                checks.append("selected-access-enters-chat-no-content")
                js = "NIQ.$('chat-model-button').click()"
            elif stage == 6:
                if data["models"] != 2:
                    return
                assert data["modelLabel"] == "Luna" and data["recommended"]
                checks.append("composer-model-picker-luna-recommended")
                window.grab().save(str(home / "chat.png"))
                finish()
                return
            stage += 1
            window.web_view.page().runJavaScript(js)
        except Exception as exc:
            finish(str(exc))

    def tick():
        nonlocal pending
        if time.monotonic() - started > 60:
            finish("Timeout at " + str(stage))
        elif not pending:
            pending = True
            window.web_view.page().runJavaScript("""JSON.stringify({ready:!!window.NIQ?.onboarding,
                onboarding:!!window.NIQ?.state.onboarding, account:document.querySelectorAll('.provider-choice').length===7,
                noContinue:!document.getElementById('onboarding-next'),keyForm:!document.getElementById('ai-key-form')?.hidden,
                noRetry:!document.getElementById('onboarding-retry'),turkish:document.getElementById('ai-chatgpt')?.textContent.includes('ChatGPT ile devam et'),
                noProviderHeading:!document.getElementById('ai-provider-label'),plan:!!document.getElementById('niq-plan')?.open,
                centered:(()=>{const r=document.getElementById('onboarding-account').getBoundingClientRect();return Math.abs(r.x+r.width/2-document.documentElement.clientWidth/2)<3;})(),
                wideButton:(()=>{const b=document.getElementById('ai-add').getBoundingClientRect(),f=document.getElementById('ai-key-form').getBoundingClientRect();return Math.abs(b.width-f.width)<3;})(),
                approval:!!document.getElementById('confirm')?.open,models:document.querySelectorAll('.model-option').length,
                modelLabel:document.getElementById('chat-model-label')?.textContent,recommended:document.getElementById('chat-model-options')?.textContent.includes('Önerilen'),
                permission:!document.getElementById('onboarding-databases')?.hidden,
                dock:!document.getElementById('dock')?.hidden,databases:document.querySelectorAll('.db-catalog-item input').length})""", result)
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(250)
    sys.exit(qt.exec())


if __name__ == "__main__":
    main()
