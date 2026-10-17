"""Seçilen yerel kaynağın bilgilerini izinle alır; sırlar yalnızca bellekte kalır."""
import json
import secrets
import subprocess
import threading
import time
from dataclasses import replace
from typing import Any, Dict

from backend.db import diagnostics
from backend.db.profiles import Profile


class ConnectionGrants:
    def __init__(self):
        self._items = {}
        self._lock = threading.Lock()

    def issue(self, profile: Profile, password: str, *, database_locked: bool = False) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if v[0] > time.monotonic()}
            if len(self._items) >= 512:
                self._items.pop(next(iter(self._items)))
            self._items[token] = (time.monotonic() + 600, profile, password, database_locked)
        return token

    def resolve(self, token: str, data: Dict[str, Any]):
        with self._lock:
            item = self._items.get(token)
        if not item or item[0] <= time.monotonic():
            raise ValueError("Bağlantı izni sona erdi. Kaynağı yeniden seçin.")
        _, profile, password, locked = item
        if locked and data.get("database", profile.database) != profile.database:
            raise ValueError("Bu izin yalnızca seçilen veritabanı için geçerli.")
        # İzin, seçilen sunucu/hesap içindir. Formdan başka sunucuya sır taşınamaz.
        for key in ("type", "host", "port", "user"):
            if data.get(key) not in (None, "", getattr(profile, key)):
                raise ValueError("Sunucu veya hesap değişti. Kaynağı yeniden seçin.")
        if any(data.get(key) and data.get(key) != profile.options.get(key) for key in ("ssh_host", "ssh_user", "ssh_key")) or data.get("ssh_password"):
            raise ValueError("SSH bağlantısı için bilgileri ayrı girin; yerel sunucu izni SSH'a taşınamaz.")
        options = {**profile.options, **{key: data[key] for key in ("ssl", "ssl_ca", "driver") if key in data}}
        profile = replace(profile, database=data.get("database", profile.database), name=data.get("name") or profile.name,
                          options=options)
        return profile, data.get("password") or password


def selected_docker_source(source_id: str) -> Dict[str, Any]:
    source = next((s for s in diagnostics._docker_servers() if s["source_id"] == source_id), None)
    if source is None:
        raise ValueError("Sunucu artık bulunamıyor. Bilgisayardakileri yeniden bul.")
    return source


def read_docker_connection(source: Dict[str, Any]):
    # Env çıktısı loglanmaz ve istemciye gönderilmez. Yalnızca DB anahtarları kullanılır.
    try:
        result = subprocess.run(["docker", "inspect", "--format", "{{json .Config.Env}}", source["container_id"]],
                                capture_output=True, text=True, timeout=5,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Sunucunun bağlantı bilgileri alınamadı. Yerel sunucuyu yeniden kontrol et.") from exc
    if result.returncode:
        raise ValueError("Sunucunun bağlantı bilgileri alınamadı.")
    values = json.loads(result.stdout)
    if not isinstance(values, list):
        raise ValueError("Sunucu bağlantı ayarları bulunamadı.")
    env = dict(item.split("=", 1) for item in values if isinstance(item, str) and "=" in item)
    if source["type"] == "postgresql":
        user = env.get("POSTGRES_USER") or "postgres"
        password = env.get("POSTGRES_PASSWORD", "")
        database = env.get("POSTGRES_DB") or user
    elif source["type"] == "mysql":
        prefix = "MARIADB" if any(k.startswith("MARIADB_") for k in env) else "MYSQL"
        user = env.get(prefix + "_USER") or "root"
        password = env.get(prefix + ("_PASSWORD" if user != "root" else "_ROOT_PASSWORD"), "")
        database = env.get(prefix + "_DATABASE", "")
    else:
        user, database = "sa", ""
        password = env.get("MSSQL_SA_PASSWORD") or env.get("SA_PASSWORD", "")
    profile = Profile(type=source["type"], host=source["host"], port=source["port"], user=user, database=database)
    return profile, password
