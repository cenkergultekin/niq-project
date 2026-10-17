"""
Desteklenen veritabanı türleri.

Yeni bir tür eklemek için bu kayıt defterine bir `Dialect` eklenir; arayüz
(tür seçimi, varsayılan port, ipuçları), bağlantı, şema okuma, SQL doğrulama ve
AI istemi bu tanımdan beslenir.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import quote, quote_plus


@dataclass(frozen=True)
class Dialect:
    id: str
    label: str
    sqlglot: str                 # sqlglot lehçe adı
    sql_name: str                # AI isteminde geçen ad
    quoting: str                 # AI'a identifier alıntılama kuralı
    default_port: Optional[int]
    default_schema: Optional[str]
    driver_module: str           # import edilebilirlik kontrolü için
    install_hint: str
    host_suggestions: List[str] = field(default_factory=list)
    file_based: bool = False
    # Güvenlik ağı olarak salt-okunur oturum ayarı (SQL, bağlantı açılışında)
    readonly_sql: List[str] = field(default_factory=list)
    timeout_sql: Optional[str] = None            # {ms} (milisaniye) ve {s} (saniye) yer tutucuları
    timeout_sql_fallback: Optional[str] = None   # birincisi desteklenmiyorsa (ör. MariaDB)
    forbidden_functions: List[str] = field(default_factory=list)
    permission_hint: str = ""
    # Sistem şemaları (tablo listesinde gizlenir)
    system_schemas: List[str] = field(default_factory=list)

    def public(self) -> Dict[str, Any]:
        """Arayüze gönderilen açıklama."""
        return {
            "id": self.id, "label": self.label, "default_port": self.default_port,
            "file_based": self.file_based, "host_suggestions": self.host_suggestions,
            "permission_hint": self.permission_hint,
        }


DIALECTS: Dict[str, Dialect] = {
    "mssql": Dialect(
        id="mssql", label="SQL Server", sqlglot="tsql", sql_name="Microsoft SQL Server (T-SQL)",
        quoting="quote names containing spaces or reserved words with [brackets]",
        default_port=1433, default_schema="dbo", driver_module="pyodbc",
        install_hint="ODBC Driver 17/18 for SQL Server kurulu olmalı: https://aka.ms/odbc18",
        host_suggestions=["localhost", "127.0.0.1", ".\\SQLEXPRESS", "(localdb)\\MSSQLLocalDB"],
        readonly_sql=["SET LOCK_TIMEOUT 5000"],
        forbidden_functions=["OPENROWSET", "OPENQUERY", "OPENDATASOURCE", "OPENXML", "XP_CMDSHELL", "SP_EXECUTESQL"],
        permission_hint="Salt-okunur kullanıcı için: CREATE LOGIN niq_ro ...; CREATE USER niq_ro ...; ALTER ROLE db_datareader ADD MEMBER niq_ro;",
        system_schemas=["sys", "INFORMATION_SCHEMA", "guest"],
    ),
    "postgresql": Dialect(
        id="postgresql", label="PostgreSQL", sqlglot="postgres", sql_name="PostgreSQL",
        quoting='quote mixed-case or special-character names with "double quotes"',
        default_port=5432, default_schema="public", driver_module="psycopg",
        install_hint="Sürücü uygulamayla gelir (psycopg).",
        host_suggestions=["localhost", "127.0.0.1"],
        readonly_sql=["SET TRANSACTION READ ONLY"],
        timeout_sql="SET statement_timeout = {ms}",
        forbidden_functions=["PG_READ_FILE", "PG_READ_BINARY_FILE", "PG_LS_DIR", "LO_IMPORT", "LO_EXPORT",
                             "DBLINK", "DBLINK_EXEC", "PG_SLEEP", "SET_CONFIG", "PG_TERMINATE_BACKEND"],
        permission_hint="Salt-okunur kullanıcı için: CREATE ROLE niq_ro LOGIN PASSWORD '...'; GRANT pg_read_all_data TO niq_ro;",
        system_schemas=["pg_catalog", "information_schema", "pg_toast"],
    ),
    "mysql": Dialect(
        id="mysql", label="MySQL / MariaDB", sqlglot="mysql", sql_name="MySQL / MariaDB",
        quoting="quote names containing spaces or reserved words with `backticks`",
        default_port=3306, default_schema=None, driver_module="pymysql",
        install_hint="Sürücü uygulamayla gelir (PyMySQL).",
        host_suggestions=["localhost", "127.0.0.1"],
        readonly_sql=["SET SESSION TRANSACTION READ ONLY"],
        timeout_sql="SET SESSION max_execution_time = {ms}",
        timeout_sql_fallback="SET SESSION max_statement_time = {s}",  # MariaDB
        forbidden_functions=["LOAD_FILE", "SLEEP", "BENCHMARK", "GET_LOCK", "SYS_EXEC", "SYS_EVAL"],
        permission_hint="Salt-okunur kullanıcı için: CREATE USER 'niq_ro'@'%' IDENTIFIED BY '...'; GRANT SELECT ON mydb.* TO 'niq_ro'@'%';",
        system_schemas=["information_schema", "performance_schema", "mysql", "sys"],
    ),
    "sqlite": Dialect(
        id="sqlite", label="SQLite (dosya)", sqlglot="sqlite", sql_name="SQLite",
        quoting='quote names containing spaces with "double quotes"',
        default_port=None, default_schema=None, driver_module="sqlite3",
        install_hint="Ek kurulum gerekmez.", file_based=True,
        readonly_sql=["PRAGMA query_only = ON"],
        forbidden_functions=["LOAD_EXTENSION", "READFILE", "WRITEFILE", "FTS3_TOKENIZER"],
        permission_hint="Dosya salt-okunur açılır (mode=ro).",
    ),
}


def get_dialect(dialect_id: str) -> Dialect:
    try:
        return DIALECTS[dialect_id]
    except KeyError:
        raise ValueError(f"Desteklenmeyen veritabanı türü: {dialect_id}") from None


def driver_available(dialect: Dialect) -> bool:
    try:
        __import__(dialect.driver_module)
        return True
    except ImportError:
        return False


def build_url(dialect_id: str, host: str = "", port: Optional[int] = None, database: str = "",
              user: str = "", password: str = "", options: Optional[Dict[str, str]] = None) -> str:
    """SQLAlchemy bağlantı adresi. Parola URL'ye gömülür; hiçbir yere loglanmaz/saklanmaz."""
    options = dict(options or {})
    auth = ""
    if user:
        auth = quote(user, safe="") + (":" + quote(password, safe="") if password else "") + "@"
    host = (host or "localhost").strip()
    port = port or get_dialect(dialect_id).default_port

    if dialect_id == "sqlite":
        path = (database or "").replace("\\", "/")
        return f"sqlite:///file:{quote_plus(path, safe='/:')}?mode=ro&uri=true"
    if dialect_id == "mssql":
        from backend.core.utils import ODBCUtils
        try:
            import pyodbc
            # SQLAlchemy NullPool tek başına yetmez: ODBC'nin kendi havuzu girişleri açık tutar.
            # Bağlantıyı kapatınca kullanıcı oturumu da gerçekten kapanmalı.
            pyodbc.pooling = False
        except ImportError:
            pass  # Sürücü eksikliğini teşhis adımı açıklar.
        driver = ODBCUtils.pick_driver(options.pop("driver", None))
        server = host if "\\" in host or not port else f"{host},{port}"
        def odbc_value(value):
            return "{" + value.replace("}", "}}") + "}"

        parts = ["DRIVER=" + odbc_value(driver), "SERVER=" + odbc_value(server), "DATABASE=" + odbc_value(database)]
        if options.pop("auth", "sql") == "windows":
            parts.append("Trusted_Connection=yes")
        else:
            parts += ["UID=" + odbc_value(user), "PWD=" + odbc_value(password)]
        ssl = options.pop("ssl", "")
        trust_default = options.pop("trust_server_cert", "yes") != "no"
        if ssl == "disable":
            parts.append("Encrypt=no")
        elif ssl == "require":
            parts += ["Encrypt=yes", "TrustServerCertificate=yes"]
        elif ssl == "verify":
            parts += ["Encrypt=yes", "TrustServerCertificate=no"]
        elif "18" in driver and trust_default:
            parts.append("TrustServerCertificate=yes")
        options.pop("ssl_ca", None)
        parts += [f"{k}={v}" for k, v in options.items() if not k.startswith("ssh_")]
        return "mssql+pyodbc:///?odbc_connect=" + quote_plus(";".join(parts))
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    database = quote(database, safe="")
    if dialect_id == "postgresql":
        params = {"disable": "disable", "require": "require", "verify": "verify-full"}
        query = ""
        if options.get("ssl") in params:
            query = "?sslmode=" + params[options["ssl"]]
            if options.get("ssl") == "verify" and options.get("ssl_ca"):
                query += "&sslrootcert=" + quote_plus(options["ssl_ca"])
        return f"postgresql+psycopg://{auth}{host}:{port}/{database}{query}"
    if dialect_id == "mysql":
        return f"mysql+pymysql://{auth}{host}:{port}/{database}?charset=utf8mb4"
    raise ValueError(f"Desteklenmeyen veritabanı türü: {dialect_id}")
