"""
NIQ - Natural Intelligence Query (masaüstü)
PySide6 + Qt WebEngine; Flask arka planda waitress ile rastgele boş bir yerel portta çalışır.
"""
import logging
import socket
import sys
import threading
import urllib.request
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice, QObject, QSize, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEnginePermission, QWebEngineScript, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)
from waitress.server import create_server

from app import app as flask_app
from backend.services.jobs import start_background_services

logger = logging.getLogger("niq.desktop")
APP_VERSION = "3.0"
ASSETS = Path(__file__).resolve().parent / "frontend" / "static" / "images"


def control_icon(kind: str) -> QIcon:
    image = QPixmap(24, 24)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#b8c9e6"), 1.6))
    if kind == "close":
        painter.drawLine(8, 8, 16, 16)
        painter.drawLine(16, 8, 8, 16)
    elif kind == "minimize":
        painter.drawLine(7, 12, 17, 12)
    elif kind == "maximize":
        painter.drawRoundedRect(7, 7, 10, 10, 1, 1)
    elif kind == "restore":
        painter.drawRect(9, 6, 9, 9)
        painter.drawRect(6, 9, 9, 9)
    elif kind == "stop":
        painter.drawRoundedRect(8, 8, 8, 8, 1, 1)
    else:
        painter.drawArc(6, 6, 12, 12, 40 * 16, 290 * 16)
        painter.drawLine(18, 5, 18, 10)
        painter.drawLine(18, 10, 13, 10)
    painter.end()
    return QIcon(image)


class TitleBar(QWidget):
    """Yerel taşıma ve pencere kontrolleri web içeriğinden bağımsız kalır."""

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().toggle_maximized()


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
        origin = QUrl(self._origin)
        local = url.scheme() == origin.scheme() and url.host() == origin.host() and url.port() == origin.port()
        if is_main_frame and not local:
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class DesktopBridge(QObject):
    @Slot(result=bool)
    def copyMcpConfiguration(self):
        from backend.routes.ai_accounts import mcp_configuration
        QApplication.clipboard().setText(mcp_configuration())
        return True

    @Slot(result=str)
    def chooseSqliteFile(self):
        path, _ = QFileDialog.getOpenFileName(self.parent(), "SQLite veritabanını seç", "",
                                             "SQLite (*.db *.sqlite *.sqlite3 *.db3)")
        return path


class NIQDesktopApp(QMainWindow):
    def __init__(self):
        super().__init__()
        from backend.services import desktop_actions
        desktop_actions.available = True
        self.focus_timer = QTimer(self)
        self.focus_timer.timeout.connect(self._focus_from_browser)
        self.focus_timer.start(250)
        self.port = free_port()
        self.origin = f"http://127.0.0.1:{self.port}"
        self.server = create_server(flask_app, host="127.0.0.1", port=self.port, threads=8)
        self.server_thread = threading.Thread(target=self.server.run, daemon=True)
        self.server_thread.start()
        self.dev_view = None

        self.setWindowTitle("NIQ - Natural Intelligence Query")
        self.setWindowIcon(QIcon(str(ASSETS / "niq.ico")))
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.resize(1400, 900)
        self.setMinimumSize(1100, 700)

        self.web_view = QWebEngineView()
        self.web_view.setPage(LocalOnlyPage(self.web_view, self.origin))
        self.web_view.page().setBackgroundColor(QColor("#081324"))
        self.web_channel = QWebChannel(self.web_view.page())
        self.desktop_bridge = DesktopBridge(self)
        self.web_channel.registerObject("desktop", self.desktop_bridge)
        self.web_view.page().setWebChannel(self.web_channel)
        # HTTP sayfası qrc:// scriptlerini yükleyemez. Qt'nin kendi köprü kodunu
        # yerel kaynaktan, herhangi bir sayfa kodu çalışmadan önce enjekte et.
        channel_file = QFile(":/qtwebchannel/qwebchannel.js")
        if not channel_file.open(QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError("Qt WebChannel kaynağı açılamadı")
        channel_script = QWebEngineScript()
        channel_script.setName("niq-desktop-bridge")
        channel_script.setSourceCode(bytes(channel_file.readAll()).decode("utf-8"))
        channel_file.close()
        channel_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        channel_script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        channel_script.setRunsOnSubFrames(False)
        self.web_view.page().scripts().insert(channel_script)
        self.web_view.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        shell = QWidget()
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.title_bar = TitleBar()
        self.title_bar.setObjectName("titleBar")
        self.title_bar.setFixedHeight(46)
        controls = QHBoxLayout(self.title_bar)
        controls.setContentsMargins(18, 0, 8, 0)
        controls.setSpacing(4)
        mark = QLabel()
        mark.setPixmap(QPixmap(str(ASSETS / "niq.png")).scaled(26, 26, Qt.AspectRatioMode.KeepAspectRatio,
                                                             Qt.TransformationMode.SmoothTransformation))
        mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        controls.addWidget(mark)
        label = QLabel("NIQ")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        controls.addWidget(label)
        controls.addStretch()

        def button(kind, title, callback):
            result = QPushButton(control_icon(kind), "")
            result.setObjectName(kind)
            result.setToolTip(title)
            result.setAccessibleName(title)
            result.setFixedSize(38, 32)
            result.setIconSize(QSize(24, 24))
            result.clicked.connect(callback)
            controls.addWidget(result)
            return result

        button("minimize", "Simge durumuna küçült", self.showMinimized)
        self.maximize_button = button("maximize", "Büyüt", self.toggle_maximized)
        button("close", "Kapat (Ctrl+Q)", self.close)
        layout.addWidget(self.title_bar)
        layout.addWidget(self.web_view, 1)
        self.setCentralWidget(shell)
        self.setStyleSheet("""
            QMainWindow, QWidget#titleBar { background: #081324; }
            QWidget#titleBar QLabel { color: #dceaff; font: 600 12px 'Segoe UI'; padding-left: 7px; }
            QWidget#titleBar QPushButton { border: 0; border-radius: 7px; background: transparent; }
            QWidget#titleBar QPushButton:hover { background: #1a3151; }
            QWidget#titleBar QPushButton:focus { border: 1px solid #5f9cfb; }
            QWidget#titleBar QPushButton#close:hover { background: #b8374f; }
            QMenuBar, QMenu, QStatusBar { background: #081324; color: #a9bddc; }
            QMenuBar::item:selected, QMenu::item:selected { background: #1a3151; }
            QMessageBox { background: #eef5ff; }
        """)
        self._build_menu()
        self.menuBar().hide()
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().addPermanentWidget(QSizeGrip(self))
        self.statusBar().showMessage("NIQ başlatılıyor...")

        self._attempts = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._load_when_ready)
        self.timer.start(300)

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, "maximize_button"):
            maximized = self.isMaximized()
            self.maximize_button.setIcon(control_icon("restore" if maximized else "maximize"))
            self.maximize_button.setToolTip("Önceki boyuta dön" if maximized else "Büyüt")
            self.maximize_button.setAccessibleName(self.maximize_button.toolTip())

    def nativeEvent(self, event_type, message):
        # Çerçevesiz Windows penceresinde kenardan yeniden boyutlandırmayı koru.
        if sys.platform == "win32" and not self.isMaximized() and not self.isFullScreen():
            import ctypes
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0084:  # WM_NCHITTEST
                rect = wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(wintypes.HWND(int(self.winId())), ctypes.byref(rect))
                x = ctypes.c_short(msg.lParam & 0xFFFF).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                margin = round(6 * self.devicePixelRatioF())
                left, right = x < rect.left + margin, x >= rect.right - margin
                top, bottom = y < rect.top + margin, y >= rect.bottom - margin
                hit = (13 if left else 14 if right else 12) if top else (
                    (16 if left else 17 if right else 15) if bottom else (10 if left else 11 if right else None))
                if hit:
                    return True, hit
        return super().nativeEvent(event_type, message)

    def _build_menu(self) -> None:
        def action(text, shortcut, slot):
            act = QAction(text, self)
            if shortcut:
                act.setShortcut(shortcut)
            act.triggered.connect(slot)
            self.addAction(act)
            return act

        file_menu = self.menuBar().addMenu("Dosya")
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
            self.web_view.setFocus()
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
                self.shutdown()
            finally:
                event.accept()
        else:
            event.ignore()

    def shutdown(self) -> None:
        """İstek işçileri bitmeden soketleri kapatmak Windows'ta WinError 10038 üretir."""
        self.timer.stop()
        self.focus_timer.stop()
        from backend.services import desktop_actions
        desktop_actions.available = False
        desktop_actions.focus_requested.clear()
        self.web_view.stop()
        self.server.task_dispatcher.shutdown(timeout=5)
        self.server.close()
        self.server_thread.join(timeout=2)

    def _focus_from_browser(self):
        from backend.services import desktop_actions
        if desktop_actions.focus_requested.is_set():
            desktop_actions.focus_requested.clear()
            if self.isMinimized():
                self.showNormal()
            self.raise_()
            self.activateWindow()


def main() -> None:
    from backend.config.config import Settings
    flask_app.config["ONBOARDING_REQUIRED"] = "--onboarding" in sys.argv or not Settings.load()["onboarding_complete"]
    qt_app = QApplication([arg for arg in sys.argv if arg not in ("--demo", "--onboarding")])
    if "--demo" in sys.argv:
        from backend.db.demo import demo_database
        from backend.db.profiles import Profile
        from backend.routes.routes import state
        with flask_app.test_request_context():
            state.connect(Profile(type="sqlite", name="Örnek satışlar", database=str(demo_database())), None)
    qt_app.setApplicationName("NIQ")
    qt_app.setWindowIcon(QIcon(str(ASSETS / "niq.ico")))
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NIQ.Desktop.3")
    qt_app.setApplicationVersion(APP_VERSION)
    qt_app.setStyle("Fusion")
    if not flask_app.config["ONBOARDING_REQUIRED"]:
        start_background_services()
    window = NIQDesktopApp()
    window.show()
    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()
