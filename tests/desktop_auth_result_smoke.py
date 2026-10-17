"""Render success/error callback pages with real Qt, local fonts and mobile layout."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    home = Path(tempfile.mkdtemp(prefix="niq-auth-design-"))
    os.environ.update(NIQ_HOME=str(home), KEYRING_SERVICE="niq-test-" + home.name, OPENAI_API_KEY="", OPENROUTER_API_KEY="")
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication

    import main_desktop
    from backend.routes.ai_accounts import _results
    _results.update({"synthetic-success": {"success": True, "provider": "chatgpt", "message": "Hesabın NIQ'ye bağlandı.", "expires": time.time() + 300},
                     "synthetic-error": {"success": False, "provider": "chatgpt", "message": "Giriş süresi doldu. NIQ'den yeni giriş başlat.", "expires": time.time() + 300}})
    qt = QApplication(sys.argv[:1])
    window = main_desktop.NIQDesktopApp()
    window.show()
    started, stage, pending = time.monotonic(), 0, False
    checks = []

    def finish(error=None):
        timer.stop()
        print(json.dumps({"passed": error is None, "checks": checks, "error": error, "screenshots": str(home)}), flush=True)
        window.shutdown()
        qt.exit(1 if error else 0)

    def result(raw):
        nonlocal stage, pending
        pending = False
        try:
            data = json.loads(raw or "{}")
            if "app" not in data:
                return
            if stage == 0:
                if not data["app"]:
                    return
                window.web_view.load(QUrl(window.origin + "/auth/result/synthetic-success"))
            else:
                title = "Giriş başarılı" if stage == 1 else "Giriş tamamlanamadı"
                if data["title"] != title or not data["fonts"] or not data["images"]:
                    return
                assert abs(data["centerX"] - data["width"] / 2) < 3 and not data["overflow"]
                assert "Poppins" in data["headingFont"] and "Manrope" in data["bodyFont"]
                if stage == 1:
                    window.grab().save(str(home / "success.png"))
                    checks.append("success-centered-brand-fonts")
                    window.web_view.load(QUrl(window.origin + "/auth/result/synthetic-error"))
                elif stage == 2:
                    window.grab().save(str(home / "failure.png"))
                    checks.append("failure-centered-brand-fonts")
                    window.setMinimumSize(320, 400)
                    window.resize(390, 844)
                elif stage == 3:
                    if data["width"] > 400:
                        return
                    window.grab().save(str(home / "mobile.png"))
                    checks.append("mobile-no-overflow")
                    finish()
                    return
            stage += 1
        except Exception as exc:
            finish(str(exc))

    def tick():
        nonlocal pending
        if time.monotonic() - started > 45:
            finish("Timeout at " + str(stage))
        elif not pending:
            pending = True
            window.web_view.page().runJavaScript("""JSON.stringify({app:!!window.NIQ,
                title:document.querySelector('h1')?.textContent,fonts:document.fonts.status==='loaded',
                images:document.images.length===2&&[...document.images].every(i=>i.complete&&i.naturalWidth>0),
                centerX:(document.querySelector('main')?.getBoundingClientRect().left||0)+(document.querySelector('main')?.getBoundingClientRect().width||0)/2,
                width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,
                headingFont:document.querySelector('h1')?getComputedStyle(document.querySelector('h1')).fontFamily:'',bodyFont:getComputedStyle(document.body).fontFamily})""", result)
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(300)
    sys.exit(qt.exec())


if __name__ == "__main__":
    main()
