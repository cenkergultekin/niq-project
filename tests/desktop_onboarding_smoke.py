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
    from backend.routes import onboarding
    from backend.routes.routes import state
    from backend.services import ai_auth

    calls = []
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

    def finish(error=None):
        timer.stop()
        print(json.dumps({"passed": error is None, "checks": checks, "calls": calls, "error": error, "screenshots": str(home)}), flush=True)
        window.shutdown()
        qt.exit(1 if error else 0)

    def result(raw):
        nonlocal stage, pending
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
                window.grab().save(str(home / "login.png"))
                checks.append("login-options-no-browser-no-discovery")
                js = "NIQ.$('onboarding-next').click()"
            elif stage == 1:
                if not data["permission"]:
                    return
                assert calls == [] and not data["dock"]
                checks.append("names-consent-required")
                js = "NIQ.$('onboarding-discover').click()"
            elif stage == 2:
                if data["databases"] != 2:
                    return
                assert calls == ["names"] and state.connector is None
                checks.append("names-only-no-connection")
                js = "const xs=[...document.querySelectorAll('.db-catalog-item input')]; xs[1].click(); NIQ.$('db-scope-connect-btn').click()"
            elif stage == 3:
                if data["onboarding"]:
                    return
                assert state.connector is not None and len(state.workspace) == 1 and calls == ["names"]
                checks.append("selected-access-enters-chat-no-content")
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
                onboarding:!!window.NIQ?.state.onboarding, account:!!document.getElementById('ai-new-provider')?.options.length,
                permission:!document.getElementById('onboarding-databases')?.hidden,
                dock:!document.getElementById('dock')?.hidden,databases:document.querySelectorAll('.db-catalog-item input').length})""", result)
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(250)
    sys.exit(qt.exec())


if __name__ == "__main__":
    main()
