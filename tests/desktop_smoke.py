"""Gerçek Qt penceresinde mikrofon -> WAV -> yerel transkript -> SQLite sorgusu.

python tests/desktop_smoke.py            sentetik Chromium mikrofonu
python tests/desktop_smoke.py --real-mic fiziksel mikrofon; düğmeye basıp durdurun
Ses dışarı gönderilmez/kaydedilmez. Transkript ve AI test dublörüdür.
"""
import argparse
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-mic", action="store_true")
    args = parser.parse_args()
    home = tempfile.mkdtemp(prefix="niq-desktop-check-")
    os.environ.update(NIQ_HOME=home, KEYRING_SERVICE="niq-test-" + Path(home).name,
                      OPENAI_API_KEY="", OPENROUTER_API_KEY="")
    if not args.real_mic:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--use-fake-device-for-media-stream"

    from PySide6.QtCore import QTimer
    from PySide6.QtWebEngineCore import QWebEnginePermission
    from PySide6.QtWidgets import QApplication

    import main_desktop
    from backend.routes.routes import state

    evidence = {"mode": "physical" if args.real_mic else "synthetic", "permissions": [], "transcribed": False,
                "queried": False, "passed": False}
    started = time.monotonic()
    path = Path(home) / "smoke.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("CREATE TABLE orders(id INTEGER PRIMARY KEY); INSERT INTO orders VALUES(1),(2),(3);")

    class TestAI:
        ai_available = True
        model = "local-test-double"
        config = {"provider": "local"}

        def suggest_questions(self, *args, **kwargs):
            return ["Kaç sipariş var?"]

        def generate_sql(self, *args, **kwargs):
            evidence["queried"] = True
            return {"sql": "SELECT COUNT(*) AS total FROM orders", "explanation": "Sipariş sayısı", "follow_ups": []}

        def transcribe(self, audio, *args, **kwargs):
            with wave.open(io.BytesIO(audio)) as wav:
                assert wav.getframerate() == 16000 and wav.getnchannels() == 1 and wav.getsampwidth() == 2
                frames = wav.getnframes()
                assert frames > 4000
                evidence.update(transcribed=True, audio_frames=frames, audio_seconds=round(frames / 16000, 2))
            return "Kaç sipariş var?"

    class TestPage(main_desktop.LocalOnlyPage):
        def _on_permission(self, permission):
            evidence["permissions"].append({"origin_local": permission.origin().toString().rstrip("/") == self._origin,
                                            "audio": permission.permissionType() == QWebEnginePermission.PermissionType.MediaAudioCapture})
            super()._on_permission(permission)

    main_desktop.LocalOnlyPage = TestPage
    state.ai_service = TestAI()
    qt = QApplication(sys.argv[:1])
    window = main_desktop.NIQDesktopApp()
    window.setWindowTitle("NIQ - Local microphone test")
    window.show()
    phase = "loading"

    def finish(code):
        evidence["passed"] = code == 0
        print(json.dumps(evidence), flush=True)
        window.shutdown()
        if state.connector:
            state.connector.close()
        qt.exit(code)

    def poll():
        if time.monotonic() - started > (180 if args.real_mic else 45):
            evidence["error"] = "Desktop smoke timeout"
            finish(1)
            return
        window.web_view.page().runJavaScript("JSON.stringify({ready:!!window.NIQ?.connect && "
                                            "document.querySelectorAll('#db-types button').length===4, connected:!!window.NIQ?.state.connected, "
                                            "recording:!!window.NIQ?.voice.recording, text:document.getElementById('stream')?.innerText, "
                                            "draft:document.getElementById('q')?.value, ghost:document.getElementById('q')?.classList.contains('voice-draft'), "
                                            "micBusy:document.getElementById('mic')?.disabled, "
                                            "toasts:document.getElementById('toasts')?.innerText})", observed)

    def observed(raw):
        nonlocal phase
        if not raw:
            return
        observed_ = json.loads(raw)
        if phase == "loading" and observed_["ready"]:
            phase = "connecting"
            payload = json.dumps({"type": "sqlite", "database": str(path)})
            window.web_view.page().runJavaScript(
                "NIQ.api('/set_db',{body:" + payload + "}).then(r=>{NIQ.setConnection(r.data); NIQ.connect.hide();})")
        elif phase == "connecting" and observed_["connected"]:
            phase = "microphone"
            print("READY: click microphone, wait, click again" if args.real_mic else "READY: synthetic capture", flush=True)
            if not args.real_mic:
                window.web_view.page().runJavaScript("document.getElementById('mic').click()")
        elif phase == "microphone" and observed_["recording"]:
            phase = "recording"
            if not args.real_mic:
                QTimer.singleShot(4000, lambda: window.web_view.page().runJavaScript("document.getElementById('mic').click()"))
        elif phase == "recording" and observed_["recording"] and observed_.get("draft") == "Kaç sipariş var?":
            evidence["live_preview"] = observed_.get("ghost") is True
            assert not evidence["queried"], "dikte kendiliğinden gönderildi"
        elif phase == "recording" and not observed_["recording"] and observed_.get("draft") == "Kaç sipariş var?" and not observed_.get("micBusy"):
            evidence["draft_ready"] = not observed_.get("ghost")
            evidence["no_auto_send"] = not evidence["queried"]
            phase = "sending"
            window.web_view.page().runJavaScript("document.getElementById('send').click()")
        elif phase == "sending" and evidence["queried"] and "3" in (observed_.get("text") or ""):
            evidence["track_released"] = not observed_["recording"]
            finish(0 if evidence["transcribed"] and evidence["permissions"] and evidence["track_released"]
                   and evidence.get("live_preview") and evidence.get("draft_ready") and evidence.get("no_auto_send") else 1)

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(300)
    return qt.exec()


if __name__ == "__main__":
    raise SystemExit(main())
