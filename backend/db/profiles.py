"""
Bağlantı profilleri ve bağlantı metni ayrıştırma.

Profiller `%APPDATA%/NIQ/connections.json` içinde parolasız tutulur; parolalar
OS keyring'inde (`profile:<id>`). Kullanıcı bir URL, ODBC/ADO/JDBC bağlantı
dizesi ya da dosya yolu yapıştırırsa tür ve alanlar tahmin edilir.
"""
import json
import logging
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import keyring

from backend.config.config import KEYRING_SERVICE, data_dir
from backend.db.dialects import DIALECTS

logger = logging.getLogger(__name__)

SQLITE_EXTENSIONS = (".db", ".sqlite", ".sqlite3", ".db3")
SCHEME_TO_TYPE = {
    "postgres": "postgresql", "postgresql": "postgresql", "pgsql": "postgresql",
    "mysql": "mysql", "mariadb": "mysql",
    "mssql": "mssql", "sqlserver": "mssql",
    "sqlite": "sqlite",
}


# options içinde kabul edilen anahtarlar
OPTION_KEYS = ("driver", "auth", "ssl", "ssl_ca", "ssh_host", "ssh_port", "ssh_user", "ssh_key")


@dataclass
class Profile:
    type: str
    name: str = ""
    host: str = ""
    port: Optional[int] = None
    database: str = ""
    user: str = ""
    options: Dict[str, str] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    def __post_init__(self):
        if self.type not in DIALECTS:
            raise ValueError(f"Desteklenmeyen veritabanı türü: {self.type}")
        dialect = DIALECTS[self.type]
        self.options = self._clean_options(self.options)
        self.name = (self.name or self.default_name()).strip()
        if not self.port and dialect.default_port and not dialect.file_based:
            self.port = dialect.default_port
        if not self.host and not dialect.file_based:
            self.host = "localhost"

    def _clean_options(self, options: Dict[str, Any]) -> Dict[str, str]:
        clean = {k: str(v).strip() for k, v in (options or {}).items() if k in OPTION_KEYS and str(v).strip()}
        if clean.get("auth") not in (None, "sql", "windows"):
            raise ValueError("auth 'sql' veya 'windows' olmalı")
        if clean.get("auth") == "windows" and self.type != "mssql":
            raise ValueError("Windows kimlik doğrulaması yalnızca SQL Server için geçerli")
        if clean.get("ssl") not in (None, "disable", "require", "verify"):
            raise ValueError("ssl 'disable', 'require' veya 'verify' olmalı")
        if clean.get("ssh_port") and not clean["ssh_port"].isdigit():
            raise ValueError("SSH portu sayı olmalı")
        if clean.get("ssh_host") and DIALECTS[self.type].file_based:
            raise ValueError("SQLite dosyası için SSH tüneli kullanılamaz")
        return clean

    @property
    def uses_windows_auth(self) -> bool:
        return self.options.get("auth") == "windows"

    @property
    def uses_ssh(self) -> bool:
        return bool(self.options.get("ssh_host"))

    def default_name(self) -> str:
        if DIALECTS[self.type].file_based:
            return re.split(r"[\\/]", self.database.rstrip("\\/"))[-1] or DIALECTS[self.type].label
        return f"{DIALECTS[self.type].label}: {self.host or 'localhost'}/{self.database}".strip(": /")

    @property
    def key(self) -> str:
        """Geçmişi bağlantıya göre ayıran kimlik (parolasız, profil adından bağımsız)."""
        return f"{self.type}|{self.host}|{self.port or ''}|{self.database}".lower()

    @property
    def keyring_account(self) -> str:
        return f"profile:{self.id}"

    def public(self) -> Dict[str, Any]:
        data = asdict(self)
        data["label"] = DIALECTS[self.type].label
        data["has_password"] = get_password(self) is not None
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Profile":
        allowed = {"type", "name", "host", "port", "database", "user", "options", "id"}
        clean = {k: v for k, v in data.items() if k in allowed and v is not None}
        if clean.get("port") in ("", 0):
            clean.pop("port")
        if "port" in clean:
            clean["port"] = int(clean["port"])
        for text_key in ("host", "database", "user", "name"):
            if isinstance(clean.get(text_key), str):
                clean[text_key] = clean[text_key].strip()
        return cls(**clean)


# --- parola (keyring) --------------------------------------------------------
def get_password(profile: Profile) -> Optional[str]:
    try:
        return keyring.get_password(KEYRING_SERVICE, profile.keyring_account)
    except Exception as exc:
        logger.warning("Keyring okunamadı: %s", exc)
        return None


def set_password(profile: Profile, password: str) -> None:
    keyring.set_password(KEYRING_SERVICE, profile.keyring_account, password)


def delete_password(profile: Profile) -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, profile.keyring_account)
    except keyring.errors.PasswordDeleteError:
        pass


def get_ssh_secret(profile: Profile) -> Optional[str]:
    """SSH parolası veya anahtar parolası (passphrase)."""
    try:
        return keyring.get_password(KEYRING_SERVICE, profile.keyring_account + ":ssh")
    except Exception as exc:
        logger.warning("Keyring okunamadı: %s", exc)
        return None


def set_ssh_secret(profile: Profile, secret: str) -> None:
    keyring.set_password(KEYRING_SERVICE, profile.keyring_account + ":ssh", secret)


def delete_ssh_secret(profile: Profile) -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, profile.keyring_account + ":ssh")
    except keyring.errors.PasswordDeleteError:
        pass


# --- kayıtlı profiller --------------------------------------------------------
class ProfileStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else data_dir() / "connections.json"
        self._lock = threading.Lock()

    def _read(self) -> List[Dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _write(self, items: List[Dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    def list(self) -> List[Profile]:
        profiles = []
        for item in self._read():
            try:
                profiles.append(Profile.from_dict(item))
            except (ValueError, TypeError):
                continue
        return profiles

    def get(self, ref: str) -> Optional[Profile]:
        """Kimlik ya da ada göre profil."""
        for profile in self.list():
            if ref in (profile.id, profile.name):
                return profile
        return None

    def save(self, profile: Profile) -> Profile:
        with self._lock:
            items = [i for i in self._read() if i.get("id") != profile.id]
            items.append(asdict(profile))
            self._write(items)
        return profile

    def delete(self, ref: str) -> Optional[Profile]:
        profile = self.get(ref)
        if not profile:
            return None
        with self._lock:
            self._write([i for i in self._read() if i.get("id") != profile.id])
        delete_password(profile)
        delete_ssh_secret(profile)
        return profile


# --- bağlantı metni ayrıştırma --------------------------------------------------
def _split_host_port(server: str) -> tuple:
    server = re.sub(r"^(tcp|np):", "", server.strip(), flags=re.IGNORECASE)
    match = re.match(r"^(.*?)[,:](\d+)$", server)
    if match and "\\" not in match.group(1):
        return match.group(1), int(match.group(2))
    return server, None


def _parse_kv(text: str) -> Dict[str, str]:
    pairs = {}
    for part in re.split(r";(?![^{]*})", text):
        if "=" in part:
            key, value = part.split("=", 1)
            pairs[re.sub(r"\s+", " ", key.strip().lower())] = value.strip().strip("{}")
    return pairs


def parse_connection_input(text: str) -> Dict[str, Any]:
    """
    URL, JDBC, ODBC/ADO dizesi veya dosya yolundan alanları çıkarır.
    Sonuç: {type, host, port, database, user, password, options} (bulunanlar) + 'recognized' (bool).
    """
    text = (text or "").strip().strip('"')
    if not text:
        return {"recognized": False}

    # Dosya yolu -> SQLite
    if text.lower().endswith(SQLITE_EXTENSIONS) and "://" not in text:
        return {"recognized": True, "type": "sqlite", "database": text}

    lowered = text.lower()
    # JDBC: jdbc:postgresql://..., jdbc:sqlserver://host:1433;databaseName=x;user=..;password=..
    if lowered.startswith("jdbc:sqlserver://"):
        body = text[len("jdbc:sqlserver://"):]
        host_part, _, rest = body.partition(";")
        host, port = _split_host_port(host_part)
        kv = _parse_kv(rest)
        result = {"recognized": True, "type": "mssql", "host": host, "port": port,
                  "database": kv.get("databasename") or kv.get("database", ""),
                  "user": kv.get("user", ""), "password": kv.get("password", "")}
        if kv.get("integratedsecurity", "").lower() in ("true", "yes", "sspi"):
            result["options"] = {"auth": "windows"}
        return result
    if lowered.startswith("jdbc:"):
        text = text[len("jdbc:"):]
        lowered = text.lower()

    # URL: scheme[+driver]://user:pass@host:port/db
    match = re.match(r"^([a-z][a-z0-9]*)(?:\+[a-z0-9_]+)?://", lowered)
    if match and match.group(1) in SCHEME_TO_TYPE:
        db_type = SCHEME_TO_TYPE[match.group(1)]
        normalized = re.sub(r"^[a-zA-Z0-9]+(\+[a-zA-Z0-9_]+)?://", "http://", text, count=1)
        if db_type == "sqlite":
            # sqlite:///göreli.db, sqlite:////mutlak/yol.db, sqlite:///C:/yol.db
            path = unquote(re.sub(r"^sqlite(\+\w+)?:///", "", text, flags=re.IGNORECASE).split("?")[0])
            return {"recognized": True, "type": "sqlite", "database": path}
        parsed = urlparse(normalized)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        return {"recognized": True, "type": db_type, "host": parsed.hostname or "", "port": parsed.port,
                "database": unquote(parsed.path.lstrip("/")), "user": unquote(parsed.username or ""),
                "password": unquote(parsed.password or ""), "options": {k: v for k, v in query.items() if k == "driver"}}

    # ODBC / ADO.NET (SQL Server)
    if "=" in text and ";" in text:
        kv = _parse_kv(text)
        server = kv.get("server") or kv.get("data source") or kv.get("address") or kv.get("addr") or ""
        if server or "driver" in kv:
            host, port = _split_host_port(server)
            result = {
                "recognized": True, "type": "mssql", "host": host, "port": port,
                "database": kv.get("database") or kv.get("initial catalog", ""),
                "user": kv.get("uid") or kv.get("user id") or kv.get("user", ""),
                "password": kv.get("pwd") or kv.get("password", ""),
            }
            options = {}
            if kv.get("driver"):
                options["driver"] = kv["driver"]
            integrated = (kv.get("integrated security") or kv.get("trusted_connection") or "").lower()
            if integrated in ("true", "yes", "sspi"):
                options["auth"] = "windows"
            if options:
                result["options"] = options
            return result
    return {"recognized": False}
