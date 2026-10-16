"""
NIQ - Natural Intelligence Query (masaüstü)
PySide6 + Qt WebEngine; Flask arka planda waitress ile rastgele boş bir yerel portta çalışır.
"""
import logging
import socket
import sys
import threading
import urllib.request

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEnginePermission, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
from waitress.server import create_server

from app import app as flask_app
from backend.services.jobs import start_background_services

logger = logging.getLogger("niq.desktop")
APP_VERSION = "3.0"


def free_port() -> int:
    """İşletim sisteminden boş bir yerel port ister (5000 çakışmasını ve sabit port tahminini önler)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalOnlyPage(QWebEnginePage):
    """Yalnızca yerel arayüze gezinmeye izin verir; dış bağlantılar varsayılan tarayıcıda açılır."""

    def __init__(self, parent, origin: str):
        super().__init__(parent)
        self._origin = origin
        self.permissionRequested.connect(self._on_permission)

    def _on_permission(self, permission: QWebEnginePermission) -> None:
        """Mikrofon (bas-konuş) izni yalnızca kendi yerel arayüzümüze verilir; diğer izinler reddedilir."""
        local = permission.origin().toString().rstrip("/") == self._origin
        if local and permission.permissionType() == QWebEnginePermission.PermissionType.MediaAudioCapture:
            permission.grant()
        else:
            permission.deny()

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if is_main_frame and not url.toString().startswith(self._origin):
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class NIQDesktopApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.port = free_port()
        self.origin = f"http://127.0.0.1:{self.port}"
        self.server = create_server(flask_app, host="127.0.0.1", port=self.port, threads=8)
        self.server_thread = threading.Thread(target=self.server.run, daemon=True)
        self.server_thread.start()
        self.dev_view = None

        self.setWindowTitle("NIQ - Natural Intelligence Query")
        self.resize(1400, 900)
        self.setMinimumSize(1100, 700)

        self.web_view = QWebEngineView()
        self.web_view.setPage(LocalOnlyPage(self.web_view, self.origin))
        self.web_view.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        self.setCentralWidget(self.web_view)
        self._build_menu()
        self.statusBar().showMessage("NIQ başlatılıyor...")

        self._attempts = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._load_when_ready)
        self.timer.start(300)

    def _build_menu(self) -> None:
        def action(text, shortcut, slot):
            act = QAction(text, self)
            if shortcut:
                act.setShortcut(shortcut)
            act.triggered.connect(slot)
            return act

        file_menu = self.menuBar().addMenu("Dosya")
        file_menu.addAction(action("Yenile", "F5", self.web_view.reload))
        file_menu.addSeparator()
        file_menu.addAction(action("Çıkış", "Ctrl+Q", self.close))
        view_menu = self.menuBar().addMenu("Görünüm")
        view_menu.addAction(action("Tam Ekran", "F11", self._toggle_fullscreen))
        help_menu = self.menuBar().addMenu("Yardım")
        help_menu.addAction(action("Geliştirici Araçları", "F12", self._toggle_dev_tools))
        help_menu.addAction(action("Hakkında", None, self._about))

    def _load_when_ready(self) -> None:
        self._attempts += 1
        try:
            with urllib.request.urlopen(f"{self.origin}/api/health", timeout=1) as response:
                ready = response.status == 200
        except Exception:
            ready = False
        if ready:
            self.timer.stop()
            self.web_view.load(QUrl(self.origin))
            self.statusBar().showMessage("NIQ hazır", 3000)
        elif self._attempts > 100:
            self.timer.stop()
            QMessageBox.critical(self, "NIQ", "Yerel sunucu başlatılamadı. Ayrıntılar için log dosyasına bakın.")

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _toggle_dev_tools(self) -> None:
        if self.dev_view is None:
            self.dev_view = QWebEngineView()
            self.dev_view.setWindowTitle("NIQ Geliştirici Araçları")
            self.web_view.page().setDevToolsPage(self.dev_view.page())
            self.dev_view.resize(1000, 600)
        self.dev_view.setVisible(not self.dev_view.isVisible())

    def _about(self) -> None:
        QMessageBox.about(
            self, "NIQ Hakkında",
            f"<h3>NIQ - Natural Intelligence Query</h3><p>Sürüm {APP_VERSION}</p>"
            "<p>Doğal dilde sorun, salt-okunur ve doğrulanmış T-SQL ile cevap alın.</p>",
        )

    def closeEvent(self, event) -> None:
        answer = QMessageBox.question(
            self, "NIQ Kapat", "NIQ uygulamasını kapatmak istediğinizden emin misiniz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            if self.dev_view:
                self.dev_view.close()
            try:
                self.server.close()
            finally:
                event.accept()
        else:
            event.ignore()


def main() -> None:
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("NIQ")
    qt_app.setApplicationVersion(APP_VERSION)
    qt_app.setStyle("Fusion")
    start_background_services()
    window = NIQDesktopApp()
    window.show()
    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()
