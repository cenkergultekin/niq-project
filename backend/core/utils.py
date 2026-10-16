"""
Ortak yardımcılar: doğrulama hatası, girdi temizleme, API yanıt biçimi,
loglama ve ODBC bağlantı dizesi işlemleri.
"""
import logging
import re
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional, Tuple

from backend.config.config import data_dir

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Kullanıcıya gösterilebilecek doğrulama hatası."""


class StringUtils:
    @staticmethod
    def sanitize_input(text: str, max_length: int = 1000) -> str:
        """Kontrol karakterlerini temizler ve uzunluğu sınırlar."""
        if not text:
            return ""
        sanitized = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length]
            logger.warning("Input truncated to %d characters", max_length)
        return sanitized.strip()


class ResponseFormatter:
    @staticmethod
    def format_error_response(error_message: str, error_code: str = "GENERIC_ERROR") -> Dict[str, Any]:
        return {"success": False, "error": error_message, "error_code": error_code}

    @staticmethod
    def format_success_response(data: Any, message: Optional[str] = None) -> Dict[str, Any]:
        response: Dict[str, Any] = {"success": True, "data": data}
        if message:
            response["message"] = message
        return response


def setup_logging(level: str = "INFO") -> None:
    """Konsol + dönen dosya logu (%APPDATA%/NIQ/logs/niq.log). Sorgu sonuçları loglanmaz."""
    root = logging.getLogger()
    if getattr(root, "_niq_configured", False):
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    try:
        log_dir = data_dir() / "logs"
        log_dir.mkdir(exist_ok=True)
        file_handler = RotatingFileHandler(log_dir / "niq.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        pass
    # HTTP istemcilerinin ayrıntı loglarında başlık/anahtar sızmasın
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._niq_configured = True  # type: ignore[attr-defined]


class ODBCUtils:
    """ODBC bağlantı dizesi işlemleri."""

    SENSITIVE_KEYS = {"PWD", "PASSWORD"}

    @staticmethod
    def parse_dsn(dsn: str) -> Dict[str, str]:
        """'KEY=value;...' dizesini sözlüğe çevirir; yaygın anahtar adlarını normalize eder."""
        result: Dict[str, str] = {}
        aliases = {"USER": "UID", "USERNAME": "UID", "PASSWORD": "PWD", "SERVERNAME": "SERVER",
                   "DB": "DATABASE", "DATABASE_NAME": "DATABASE"}
        for part in (p.strip() for p in (dsn or "").split(";") if p.strip()):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = key.strip().upper()
            result[aliases.get(key, key)] = value.strip()
        return result

    @staticmethod
    def build_dsn(driver: str, server: str, database: str, uid: str,
                  pwd: Optional[str] = None, extra: Optional[Dict[str, str]] = None) -> str:
        """Bağlantı dizesi üretir. pwd None ise parola eklenmez (saklama/loglama için)."""
        items: List[Tuple[str, str]] = []
        if driver:
            items.append(("DRIVER", driver if driver.startswith("{") else f"{{{driver}}}"))
        if server:
            items.append(("SERVER", server))
        if database:
            items.append(("DATABASE", database))
        if uid:
            items.append(("UID", uid))
        if pwd is not None:
            # Parolada ; veya } olabilir: ODBC kaçış kuralı
            items.append(("PWD", "{" + pwd.replace("}", "}}") + "}" if re.search(r"[;{}]", pwd) else pwd))
        for k, v in (extra or {}).items():
            if k and v and k.upper() not in {key for key, _ in items}:
                items.append((k, v))
        return ";".join(f"{k}={v}" for k, v in items)

    @staticmethod
    def mask_dsn(dsn: str) -> str:
        """Parolayı gizlenmiş bağlantı dizesi (log için)."""
        masked: List[str] = []
        for part in (p.strip() for p in (dsn or "").split(";") if p.strip()):
            key = part.split("=", 1)[0].strip().upper()
            masked.append(f"{part.split('=', 1)[0]}=***" if "=" in part and key in ODBCUtils.SENSITIVE_KEYS else part)
        return ";".join(masked)

    @staticmethod
    def pick_driver(preferred: Optional[str] = None) -> str:
        """Yüklü SQL Server sürücüsünü seçer: istenen varsa o, yoksa en yeni (18 > 17 > diğer)."""
        try:
            import pyodbc
            installed = [d for d in pyodbc.drivers() if "SQL Server" in d]
        except Exception:
            installed = []
        if preferred and preferred in installed:
            return preferred
        for candidate in sorted(installed, key=lambda d: [int(n) for n in re.findall(r"\d+", d)] or [0], reverse=True):
            return candidate
        return preferred or "ODBC Driver 18 for SQL Server"
