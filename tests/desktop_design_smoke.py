"""Gerçek Qt penceresinde logo, pencere kontrolleri, dosya köprüsü ve responsive görünüm."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    home = Path(tempfile.mkdtemp(prefix="niq-design-check-"))
    os.environ.update(NIQ_HOME=str(home), KEYRING_SERVICE="niq-test-" + home.name,
                      OPENAI_API_KEY="", OPENROUTER_API_KEY="")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton

    import main_desktop
    from backend.db.demo import demo_database
    from backend.db.profiles import Profile
    from backend.routes.routes import state

    class AI:
        ai_available = True
        model = "local-test-double"
        config = {"provider": "local"}

        def suggest_questions(self, *args, **kwargs):
            return ["Bu ayın toplam satışı", "En çok satılan ürünler", "Müşteri sayısı"]

    state.ai_service = AI()
    path = demo_database()
    with main_desktop.flask_app.test_request_context():
        state.connect(Profile(type="sqlite", name="Örnek satışlar", database=str(path)), None)
    qt = QApplication(sys.argv[:1])
    window = main_desktop.NIQDesktopApp()
    window.show()
    QFileDialog.getOpenFileName = staticmethod(lambda *args: (str(path), ""))
    started = time.monotonic()
    stage = 0
    pending = False
    passed = []

    def finish(error=None):
        timer.stop()
        print(json.dumps({"passed": not error, "checks": passed, "screenshots": str(home), "error": error}), flush=True)
        window.shutdown()
        qt.exit(1 if error else 0)

    def result(raw):
        nonlocal stage, pending
        pending = False
        try:
            data = json.loads(raw or "{}")
            if data.get("error"):
                raise AssertionError(data["error"])
            if not data.get("ready"):
                return
            if stage == 0:
                assert not window.windowIcon().isNull(), "Missing window icon"
                window.grab().save(str(home / "chat.png"))
                passed.append("chat-logo-layout")
                window.web_view.page().runJavaScript("NIQ.connect.show(); NIQ.$('db-source-file-btn').click()")
            elif stage == 1:
                assert data["file"] == str(path), "Native file picker did not fill file"
                window.web_view.page().runJavaScript("NIQ.connect.show()")
                passed.append("native-file-picker")
            elif stage == 2:
                window.grab().save(str(home / "sources.png"))
                window.web_view.page().runJavaScript("NIQ.connect.hide(); NIQ.go('settings')")
            elif stage == 3:
                window.grab().save(str(home / "settings.png"))
                window.setMinimumSize(320, 400)
                window.resize(640, 800)
                window.web_view.page().runJavaScript("NIQ.go('chat')")
            elif stage == 4:
                assert not data["overflow"], "Horizontal overflow at 640px"
                window.grab().save(str(home / "compact.png"))
                passed.append("compact-layout")
                window.maximize_button.click()
            elif stage == 5:
                assert window.isMaximized(), "Maximize failed"
                window.maximize_button.click()
            elif stage == 6:
                assert not window.isMaximized(), "Restore failed"
                window.title_bar.findChild(QPushButton, "minimize").click()
            elif stage == 7:
                assert window.isMinimized(), "Minimize failed"
                window.showNormal()
                window.reload_button.click()
            elif stage == 8:
                passed.append("minimize-maximize-restore-reload")
                QMessageBox.question = staticmethod(lambda *args: QMessageBox.StandardButton.No)
                window.title_bar.findChild(QPushButton, "close").click()
                assert window.isVisible(), "Cancelled close hid window"
                QMessageBox.question = staticmethod(lambda *args: QMessageBox.StandardButton.Yes)
                timer.stop()
                window.title_bar.findChild(QPushButton, "close").click()
                passed.append("close-cancel-and-confirm")
                print(json.dumps({"passed": True, "checks": passed, "screenshots": str(home)}), flush=True)
                qt.exit(0)
                return
            stage += 1
        except Exception as exc:
            finish(str(exc))

    def poll():
        nonlocal pending
        if time.monotonic() - started > 50:
            finish("Design smoke timeout")
        elif not pending:
            pending = True
            window.web_view.page().runJavaScript("""JSON.stringify({
                ready:!!window.NIQ?.connect && NIQ.state.connected &&
                    !NIQ.$('db-source-file-btn').hidden && document.readyState==='complete' &&
                    document.fonts.status==='loaded',
                file:window.NIQ?.$('db_database')?.value,
                overflow:document.documentElement.scrollWidth>innerWidth
            })""", result)

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(750)
    return qt.exec()


if __name__ == "__main__":
    raise SystemExit(main())
