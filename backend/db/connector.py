"""
Tür-bağımsız veritabanı bağlayıcısı (SQLAlchemy).

- Şema yalnızca istenen tablolar için, tek tek okunup kısa süre önbelleğe alınır.
- Sorgular salt-okunur oturumda çalışır, satır limitlenir, her zaman rollback edilir.
"""
import logging
import re
import threading
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from backend.config.config import Config
from backend.db.dialects import DIALECTS, build_url
from backend.db.profiles import Profile, get_password, get_ssh_secret
from backend.db.tunnel import HostKeyChanged, HostKeyUnknown, SSHTunnel, SSHTunnelError
from backend.models.models import ColumnInfo, DatabaseSchema, QueryResponse, TableInfo

logger = logging.getLogger(__name__)


def display_name(schema: Optional[str], table: str, default_schema: Optional[str]) -> str:
    """Varsayılan şema sade, diğerleri 'schema.table' görünür."""
    if not schema or (default_schema and schema.lower() == default_schema.lower()):
        return table
    return f"{schema}.{table}"


def _plain(value: Any) -> Any:
    """Decimal (PostgreSQL numeric, MySQL/SQL Server decimal) sayı olarak döner; arayüz hizalar ve biçimler."""
    return float(value) if isinstance(value, Decimal) else value


def friendly_error(exc: Exception) -> str:
    """Sürücü hata zincirinden okunabilir tek satır çıkarır."""
    text_ = str(getattr(exc, "orig", None) or exc)
    if re.search(r"HYT00|timeout expired|interrupted|statement timeout|maximum statement execution time exceeded",
                 text_, re.IGNORECASE) and not re.search(r"login|connect|unknown system variable", text_, re.IGNORECASE):
        return f"Sorgu {Config.QUERY_TIMEOUT} saniye sınırını aştı. Daha dar bir soru sorun (tarih aralığı, daha az tablo)."
    if "]" in text_ and "[" in text_:
        text_ = text_.rsplit("]", 1)[-1]
    text_ = text_.strip(" )'\"\n")
    return " ".join(text_.split())[:300] or "Veritabanı hatası"


# SQL Server şema okuma: SQLAlchemy'nin inspector'ı parametreli CAST(? AS NVARCHAR(max)) kullanır ve Windows ile gelen
# eski "SQL Server" ODBC sürücüsü bunu reddeder (HY104). Parametresiz, tek seferlik ham sorgular her sürücüyle çalışır
# ve tablo başına sorgu yerine tüm şemayı üç sorguda okur.
MSSQL_COLUMNS_SQL = """
SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, c.CHARACTER_MAXIMUM_LENGTH, c.IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS c
JOIN INFORMATION_SCHEMA.TABLES t
  ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME AND t.TABLE_TYPE = 'BASE TABLE'
WHERE c.TABLE_SCHEMA NOT IN ('sys', 'INFORMATION_SCHEMA', 'guest')
ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION
"""
MSSQL_PRIMARY_KEYS_SQL = """
SELECT ku.TABLE_SCHEMA, ku.TABLE_NAME, ku.COLUMN_NAME
FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
  ON tc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME AND tc.TABLE_SCHEMA = ku.TABLE_SCHEMA
WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
ORDER BY ku.ORDINAL_POSITION
"""
MSSQL_FOREIGN_KEYS_SQL = """
SELECT OBJECT_SCHEMA_NAME(fkc.parent_object_id), OBJECT_NAME(fkc.parent_object_id), pc.name,
       OBJECT_SCHEMA_NAME(fkc.referenced_object_id), OBJECT_NAME(fkc.referenced_object_id), rc.name
FROM sys.foreign_key_columns fkc
JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
"""


def apply_query_timeout(db_type: str, dbapi_connection, seconds: int) -> None:
    """Sürücü düzeyinde sorgu zaman aşımı. PostgreSQL/MySQL için SQL ile (`_prepare`), burada diğerleri."""
    if db_type == "mssql":
        dbapi_connection.timeout = seconds  # pyodbc: sorgu zaman aşımı (sn)
    elif db_type == "sqlite":
        deadline = time.monotonic() + seconds
        dbapi_connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)


class Connector:
    """Bir bağlantı profilinin canlı yöneticisi."""

    def __init__(self, profile: Profile, password: Optional[str] = None,
                 ssh_secret: Optional[str] = None, trust_ssh_host: bool = False):
        self.profile = profile
        self.dialect = DIALECTS[profile.type]
        self._password = password
        self._ssh_secret = ssh_secret
        self._trust_ssh_host = trust_ssh_host
        self._tunnel: Optional[SSHTunnel] = None
        self._engine: Optional[Engine] = None
        self._table_cache: Optional[List[str]] = None
        self._table_cache_at = 0.0
        self._schema_cache: Dict[str, TableInfo] = {}
        self._lock = threading.Lock()

    # --- bağlantı ---------------------------------------------------------
    @property
    def db_key(self) -> str:
        return self.profile.key

    def open_tunnel(self) -> Optional[int]:
        """Profilde SSH varsa tüneli açar ve yerel portu döndürür. HostKeyUnknown onay için yukarı iletilir."""
        if not self.profile.uses_ssh:
            return None
        if self._tunnel is not None and self._tunnel.local_port:
            return self._tunnel.local_port
        profile = self.profile
        if "\\" in profile.host:
            raise SSHTunnelError("Adlandırılmış SQL Server örneği tünellenemez; sunucu adresini host ve port olarak girin.")
        secret = self._ssh_secret if self._ssh_secret is not None else get_ssh_secret(profile)
        options = profile.options
        self._tunnel = SSHTunnel(
            options["ssh_host"], int(options.get("ssh_port") or 22), options.get("ssh_user", ""),
            profile.host or "localhost", int(profile.port or self.dialect.default_port or 0),
            key_path=options.get("ssh_key"), secret=secret, trust_new_host=self._trust_ssh_host,
        )
        return self._tunnel.start()

    def _url(self) -> str:
        options = dict(self.profile.options)
        host, port = self.profile.host, self.profile.port
        local_port = self.open_tunnel()
        if local_port:
            host, port = "127.0.0.1", local_port
        if self.profile.uses_windows_auth:
            password = ""
        else:
            password = self._password if self._password is not None else (get_password(self.profile) or "")
        if self.profile.type == "mssql":
            from backend.config.config import Settings
            options.setdefault("trust_server_cert", "yes" if Settings.load().get("trust_server_cert", True) else "no")
        return build_url(self.profile.type, host, port, self.profile.database,
                         self.profile.user, password, options)

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            connect_args: Dict[str, Any] = {}
            if self.profile.type in ("postgresql", "mysql"):
                connect_args["connect_timeout"] = Config.DB_CONNECTION_TIMEOUT
            elif self.profile.type == "mssql":
                connect_args["timeout"] = Config.DB_CONNECTION_TIMEOUT
            if self.profile.type == "mysql":
                ssl_mode = self.profile.options.get("ssl")
                if ssl_mode == "require":
                    connect_args["ssl"] = {"check_hostname": False}
                elif ssl_mode == "verify":
                    connect_args["ssl"] = {"ca": self.profile.options.get("ssl_ca")}
            engine = create_engine(self._url(), poolclass=NullPool, connect_args=connect_args)
            db_type = self.profile.type

            @event.listens_for(engine, "connect")
            def _on_connect(dbapi_connection, _record):
                apply_query_timeout(db_type, dbapi_connection, Config.QUERY_TIMEOUT)

            self._engine = engine
        return self._engine

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
        if self._tunnel is not None:
            self._tunnel.close()
            self._tunnel = None

    def _prepare(self, conn) -> None:
        """Oturumu salt-okunur ve zaman sınırlı yapar (veritabanı tarafı ikinci savunma)."""
        for statement in self.dialect.readonly_sql:
            conn.exec_driver_sql(statement)
        if self.dialect.timeout_sql:
            values = {"ms": Config.QUERY_TIMEOUT * 1000, "s": Config.QUERY_TIMEOUT}
            try:
                conn.exec_driver_sql(self.dialect.timeout_sql.format(**values))
            except Exception:
                if not self.dialect.timeout_sql_fallback:
                    raise
                # MariaDB 'max_execution_time' değişkenini tanımaz; kendi karşılığı 'max_statement_time'
                conn.exec_driver_sql(self.dialect.timeout_sql_fallback.format(**values))

    def test_connection(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1")).fetchone()
            return True
        except (HostKeyUnknown, HostKeyChanged):
            raise
        except Exception as exc:
            logger.error("Bağlantı testi başarısız: %s", friendly_error(exc))
            return False

    def list_databases(self) -> List[str]:
        """Sunucudaki veritabanı adları (dosya tabanlı türlerde boş)."""
        queries = {
            "mssql": "SELECT name FROM sys.databases WHERE state = 0 AND database_id > 4 ORDER BY name",
            "postgresql": "SELECT datname FROM pg_database WHERE NOT datistemplate AND datallowconn ORDER BY datname",
            "mysql": "SELECT schema_name FROM information_schema.schemata "
                     "WHERE schema_name NOT IN ('information_schema','performance_schema','mysql','sys') ORDER BY 1",
        }
        sql = queries.get(self.profile.type)
        if not sql:
            return []
        with self.engine.connect() as conn:
            return [row[0] for row in conn.execute(text(sql)).fetchall()]

    # --- şema -------------------------------------------------------------
    def _schemas(self, inspector) -> List[Optional[str]]:
        if self.profile.type in ("mysql", "sqlite"):
            return [None]
        hidden = {s.lower() for s in self.dialect.system_schemas}
        names = [s for s in inspector.get_schema_names()
                 if s.lower() not in hidden and not s.lower().startswith(("pg_", "db_"))]
        return names or [None]

    def _load_mssql_schema(self) -> Dict[str, TableInfo]:
        """Tüm şemayı (kolon tipi, PK, FK) üç ham sorguyla okur."""
        tables: Dict[str, TableInfo] = {}
        keys: Dict[tuple, str] = {}
        with self.engine.connect() as conn:
            for schema, table, column, dtype, length, nullable in conn.exec_driver_sql(MSSQL_COLUMNS_SQL).fetchall():
                name = display_name(schema, table, self.dialect.default_schema)
                keys[(schema.lower(), table.lower())] = name
                type_text = dtype if not length or length < 0 else f"{dtype}({length})"
                info = tables.setdefault(name, TableInfo(name=name, columns=[]))
                info.columns.append(ColumnInfo(column, type_text, nullable == "YES"))
            for schema, table, column in conn.exec_driver_sql(MSSQL_PRIMARY_KEYS_SQL).fetchall():
                name = keys.get((schema.lower(), table.lower()))
                if name:
                    tables[name].primary_key.append(column)
            try:
                for ps, pt, pc, rs, rt, rc in conn.exec_driver_sql(MSSQL_FOREIGN_KEYS_SQL).fetchall():
                    name = keys.get((ps.lower(), pt.lower()))
                    target = keys.get((rs.lower(), rt.lower()), display_name(rs, rt, self.dialect.default_schema))
                    if name:
                        tables[name].foreign_keys.append((pc, target, rc))
            except Exception as exc:  # sys.* görünümüne yetki yoksa şema yine de çalışır
                logger.warning("Foreign key bilgisi okunamadı: %s", friendly_error(exc))
            conn.rollback()
        return tables

    def get_tables(self, refresh: bool = False) -> List[str]:
        with self._lock:
            expired = time.time() - self._table_cache_at > Config.SCHEMA_CACHE_TTL
            if refresh or self._table_cache is None or expired:
                names: List[str] = []
                if self.profile.type == "mssql":
                    loaded = self._load_mssql_schema()
                    self._schema_cache = loaded
                    names = list(loaded)
                else:
                    with self.engine.connect() as conn:
                        inspector = inspect(conn)
                        for schema in self._schemas(inspector):
                            for table in inspector.get_table_names(schema=schema):
                                names.append(display_name(schema, table, self.dialect.default_schema))
                self._table_cache = sorted(names, key=str.lower)
                self._table_cache_at = time.time()
                if self.profile.type != "mssql":
                    self._schema_cache.clear()
            return list(self._table_cache)

    def quote_table(self, name: str) -> str:
        """Tablo adını türe uygun alıntılar ('schema.table' dahil)."""
        preparer = self.engine.dialect.identifier_preparer
        schema, table = self._split(name)
        if "." in name and self.profile.type not in ("mysql", "sqlite"):
            return f"{preparer.quote_schema(schema)}.{preparer.quote(table)}"
        return preparer.quote(table)

    def _split(self, name: str):
        """'schema.table' -> (schema, table); varsayılan şema için (None/varsayılan, table)."""
        if "." in name and self.profile.type not in ("mysql", "sqlite"):
            schema, table = name.split(".", 1)
            return schema, table
        return self.dialect.default_schema, name

    def get_table_info(self, name: str) -> TableInfo:
        cached = self._schema_cache.get(name)
        if cached:
            return cached
        if self.profile.type == "mssql":
            self.get_tables(refresh=True)
            if name in self._schema_cache:
                return self._schema_cache[name]
            raise ValueError(f"Tablo bulunamadı: {name}")
        schema, table = self._split(name)
        with self.engine.connect() as conn:
            inspector = inspect(conn)
            columns = [ColumnInfo(c["name"], str(c["type"]), bool(c.get("nullable", True)))
                       for c in inspector.get_columns(table, schema=schema)]
            if not columns:
                raise ValueError(f"Tablo bulunamadı: {name}")
            pk = inspector.get_pk_constraint(table, schema=schema).get("constrained_columns") or []
            fks = []
            for fk in inspector.get_foreign_keys(table, schema=schema):
                target = display_name(fk.get("referred_schema"), fk["referred_table"], self.dialect.default_schema)
                for local, remote in zip(fk["constrained_columns"], fk["referred_columns"], strict=False):
                    fks.append((local, target, remote))
        info = TableInfo(name=name, columns=columns, primary_key=list(pk), foreign_keys=fks)
        self._schema_cache[name] = info
        return info

    def get_multiple_table_columns(self, names: List[str]) -> Dict[str, List[str]]:
        return {n: self.get_table_info(n).column_names for n in names}

    def get_database_schema(self, names: Optional[List[str]] = None) -> DatabaseSchema:
        names = names if names is not None else self.get_tables()
        return DatabaseSchema(tables={n: self.get_table_info(n) for n in names})

    # --- sorgu ------------------------------------------------------------
    def execute_readonly(self, sql_query: str, row_limit: int) -> QueryResponse:
        try:
            with self.engine.connect() as conn:
                try:
                    self._prepare(conn)
                    # no_parameters: LIKE '%a%' gibi % karakterleri sürücü tarafından biçim yer tutucusu sanılmasın
                    result = conn.execution_options(no_parameters=True).exec_driver_sql(sql_query)
                    if not result.returns_rows:
                        return QueryResponse(sql_query=sql_query, error="Sorgu satır döndürmedi")
                    columns = [c or f"column_{i}" for i, c in enumerate(result.keys())]
                    rows = result.fetchmany(row_limit + 1)
                    truncated = len(rows) > row_limit
                    data = [dict(zip(columns, (_plain(v) for v in row), strict=False)) for row in rows[:row_limit]]
                    return QueryResponse(sql_query=sql_query, results=data, truncated=truncated)
                finally:
                    conn.rollback()
        except Exception as exc:
            logger.error("Sorgu çalıştırılamadı: %s", friendly_error(exc))
            return QueryResponse(sql_query=sql_query, error=friendly_error(exc))
