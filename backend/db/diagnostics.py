"""
Bağlantı teşhisi ve keşif.

`diagnose` bir bağlantıyı adım adım sınar (sürücü, ağ, giriş, okuma, yetki) ve her
başarısızlık için ne yapılacağını söyler. Hem arayüz hem agent (CLI) aynı çıktıyı kullanır.
`discover` kullanıcının yazmasına gerek kalmadan makinedeki olası sunucuları bulur.
"""
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from backend.db.connector import Connector, friendly_error
from backend.db.dialects import DIALECTS, driver_available
from backend.db.profiles import Profile
from backend.db.tunnel import HostKeyChanged, HostKeyUnknown, SSHTunnelError

OK, FAIL, WARN, SKIP = "ok", "fail", "warn", "skip"

ERROR_HINTS = [
    (r"login failed|password authentication failed|access denied|authentication failed|ORA-01017",
     "Kullanıcı adı veya parola hatalı. Parolayı kontrol edin; SQL Server'da 'SQL Server ve Windows kimlik doğrulaması' modu açık olmalı."),
    (r"cannot open database|does not exist|unknown database|database .* not found",
     "Veritabanı adı bulunamadı. 'Veritabanlarını getir' ile sunucudaki adları listeleyip seçin."),
    (r"certificate|ssl|tls",
     "Sertifika sorunu. Ayarlar > Gelişmiş > 'Sunucu sertifikasına güven' seçeneğini açın."),
    (r"timed out|timeout|refused|could not connect|can't connect|unreachable|no route|network-related|actively refused",
     "Sunucuya ulaşılamadı. Adres ve portu, sunucunun çalıştığını ve güvenlik duvarının portu açtığını kontrol edin."),
    (r"data source name not found|no driver|can't open lib|driver",
     "Sürücü bulunamadı. Gerekli ODBC sürücüsünü kurun."),
]


def explain_error(message: str) -> str:
    for pattern, hint in ERROR_HINTS:
        if re.search(pattern, message, re.IGNORECASE):
            return hint
    return "Hata ayrıntısını kontrol edin; sorun sürerse log dosyasına bakın (%APPDATA%\\NIQ\\logs\\niq.log)."


def _step(step_id: str, label: str, status: str, detail: str = "", hint: str = "") -> Dict[str, str]:
    return {"id": step_id, "label": label, "status": status, "detail": detail, "hint": hint}


def _check_driver(profile: Profile) -> Dict[str, str]:
    dialect = DIALECTS[profile.type]
    if not driver_available(dialect):
        return _step("driver", "Sürücü", FAIL, f"{dialect.driver_module} yüklenemedi", dialect.install_hint)
    if profile.type == "mssql":
        from backend.core.utils import ODBCUtils
        driver = ODBCUtils.pick_driver(profile.options.get("driver"))
        try:
            import pyodbc
            installed = [d for d in pyodbc.drivers() if "SQL Server" in d]
        except Exception:
            installed = []
        if not installed:
            return _step("driver", "Sürücü", FAIL, "SQL Server ODBC sürücüsü bulunamadı", dialect.install_hint)
        return _step("driver", "Sürücü", OK, driver)
    return _step("driver", "Sürücü", OK, dialect.driver_module)


def _check_network(profile: Profile) -> Dict[str, str]:
    dialect = DIALECTS[profile.type]
    if dialect.file_based:
        import os
        path = profile.database
        if not path or not os.path.isfile(path):
            return _step("network", "Dosya", FAIL, f"Dosya bulunamadı: {path or '(boş)'}", "Dosya yolunu kontrol edin.")
        return _step("network", "Dosya", OK, path)
    if profile.type == "mssql" and "\\" in profile.host:
        return _step("network", "Ağ", SKIP, "Adlandırılmış örnek (SQL Browser ile çözülür), port sınanmadı")
    host = profile.host
    try:
        socket.getaddrinfo(host, profile.port)
    except OSError:
        return _step("network", "Ağ", FAIL, f"'{host}' adı çözülemedi", "Sunucu adını veya IP adresini kontrol edin.")
    try:
        with socket.create_connection((host, int(profile.port)), timeout=3):
            pass
    except OSError as exc:
        hint = "Sunucu kapalı, port yanlış ya da güvenlik duvarı engelliyor olabilir."
        if profile.type == "mssql":
            hint += " SQL Server Configuration Manager'da TCP/IP protokolünü etkinleştirin."
        return _step("network", "Ağ", FAIL, f"{host}:{profile.port} erişilemiyor ({exc.__class__.__name__})", hint)
    return _step("network", "Ağ", OK, f"{host}:{profile.port} erişilebilir")


def _check_privileges(connector: Connector) -> Dict[str, str]:
    """Hesabın yazma yetkisi var mı? Varsa uyarır (uygulama yine de salt-okunur çalışır)."""
    queries = {
        "mssql": "SELECT CASE WHEN IS_MEMBER('db_owner') = 1 OR IS_SRVROLEMEMBER('sysadmin') = 1 "
                 "OR IS_MEMBER('db_datawriter') = 1 THEN 1 ELSE 0 END",
        "postgresql": "SELECT CASE WHEN rolsuper OR rolcreatedb OR rolcreaterole THEN 1 ELSE 0 END "
                      "FROM pg_roles WHERE rolname = current_user",
    }
    dialect = connector.dialect
    sql = queries.get(connector.profile.type)
    if connector.profile.type == "sqlite":
        return _step("privileges", "Yetki", OK, "Dosya salt-okunur açılır")
    try:
        with connector.engine.connect() as conn:
            if sql:
                writable = bool(conn.execute(text(sql)).scalar())
            else:  # mysql
                grants = " ".join(str(r[0]) for r in conn.execute(text("SHOW GRANTS")).fetchall()).upper()
                writable = bool(re.search(r"ALL PRIVILEGES|INSERT|UPDATE|DELETE|DROP|CREATE", grants))
    except Exception:
        return _step("privileges", "Yetki", SKIP, "Yetki kontrol edilemedi")
    if writable:
        return _step("privileges", "Yetki", WARN, "Hesabın yazma yetkisi var",
                     "NIQ sorguları salt-okunur çalıştırır, yine de salt-okunur bir kullanıcı önerilir. " + dialect.permission_hint)
    return _step("privileges", "Yetki", OK, "Salt-okunur hesap")


def diagnose(profile: Profile, password: Optional[str], ssh_secret: Optional[str] = None,
             trust_ssh_host: bool = False) -> Dict[str, Any]:
    """Bağlantıyı adım adım sınar. Başarısız adımdan sonrası atlanır.
    Bilinmeyen SSH sunucu anahtarı HostKeyUnknown olarak fırlatılır (kritik adım: kullanıcı onayı)."""
    steps: List[Dict[str, str]] = [_check_driver(profile)]
    connector = Connector(profile, password=password or "", ssh_secret=ssh_secret, trust_ssh_host=trust_ssh_host)
    try:
        if steps[-1]["status"] != FAIL and profile.uses_ssh:
            try:
                local_port = connector.open_tunnel()
                steps.append(_step("ssh", "SSH tüneli", OK, f"{profile.options['ssh_host']} üzerinden 127.0.0.1:{local_port}"))
            except HostKeyUnknown:
                raise
            except SSHTunnelError as exc:
                hint = ("Anahtar dosyasını, kullanıcı adını ve parolayı kontrol edin; sunucuda SSH'a izin verildiğinden emin olun."
                        if not isinstance(exc, HostKeyChanged) else "Parmak izi değişti: yöneticiyle doğrulamadan devam etmeyin.")
                steps.append(_step("ssh", "SSH tüneli", FAIL, str(exc), hint))
        if steps[-1]["status"] != FAIL:
            if profile.uses_ssh:
                steps.append(_step("network", "Ağ", SKIP, "SSH tüneli üzerinden erişilir"))
            else:
                steps.append(_check_network(profile))
        login_ok = False
        if steps[-1]["status"] != FAIL:
            try:
                with connector.engine.connect() as conn:
                    conn.execute(text("SELECT 1")).fetchone()
                if connector.dialect.file_based:
                    detail = "Dosya açıldı"
                elif profile.uses_windows_auth:
                    detail = "Windows hesabıyla bağlanıldı"
                else:
                    detail = f"{profile.user or 'kullanıcı'} olarak bağlanıldı"
                steps.append(_step("login", "Giriş", OK, detail))
                login_ok = True
            except HostKeyUnknown:
                raise
            except Exception as exc:
                message = friendly_error(exc)
                hint = explain_error(message)
                if profile.uses_windows_auth and re.search(r"login failed|SSPI|trusted", message, re.IGNORECASE):
                    hint = ("Windows girişi için uygulamanın çalıştığı Windows hesabının SQL Server'da yetkili olması gerekir "
                            "ve bilgisayar etki alanına (domain) bağlı olmalıdır. Aksi halde SQL hesabı kullanın.")
                steps.append(_step("login", "Giriş", FAIL, message, hint))
        if login_ok:
            try:
                tables = connector.get_tables(refresh=True)
                if tables:
                    steps.append(_step("read", "Tablo erişimi", OK, f"{len(tables)} tablo görüldü"))
                else:
                    steps.append(_step("read", "Tablo erişimi", WARN, "Hiç tablo görünmüyor",
                                       "Kullanıcıya bu veritabanında SELECT/metadata yetkisi verin veya doğru veritabanını seçin."))
            except Exception as exc:
                message = friendly_error(exc)
                steps.append(_step("read", "Tablo erişimi", FAIL, message, explain_error(message)))
            steps.append(_check_privileges(connector))
    finally:
        connector.close()
    ok = all(s["status"] in (OK, SKIP, WARN) for s in steps) and any(s["id"] == "login" and s["status"] == OK for s in steps)
    return {"ok": ok, "steps": steps}


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def discover() -> Dict[str, Any]:
    """Makinedeki sürücüleri ve varsayılan portlarda dinleyen yerel sunucuları bulur."""
    drivers = {d_id: driver_available(d) for d_id, d in DIALECTS.items()}
    odbc: List[str] = []
    try:
        import pyodbc
        odbc = [d for d in pyodbc.drivers() if "SQL Server" in d]
    except Exception:
        pass
    candidates = [(d_id, d.default_port) for d_id, d in DIALECTS.items() if d.default_port]
    with ThreadPoolExecutor(max_workers=len(candidates) or 1) as pool:
        open_flags = list(pool.map(lambda c: _port_open(c[1]), candidates))
    local = [{"type": t, "host": "localhost", "port": p, "label": DIALECTS[t].label}
             for (t, p), is_open in zip(candidates, open_flags, strict=False) if is_open]
    return {"drivers": drivers, "odbc_drivers": odbc, "local_servers": local}
