"""
Üretilen SQL için güvenlik katmanı (AST tabanlı).

Metin taraması yerine sqlglot ile T-SQL olarak ayrıştırılır; yalnızca tek bir
salt-okunur SELECT (CTE ve UNION dahil) kabul edilir. İkinci savunma hattı
veritabanı kullanıcısının salt-okunur olmasıdır; çalıştırma katmanı ayrıca
her zaman rollback yapar.
"""
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Set

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from backend.db.dialects import get_dialect

# Sorguyu sunucu dışına çıkaran veya yan etki üreten fonksiyonlar (tür bazlı liste dialects.py'de)
FORBIDDEN_FUNCTIONS: Set[str] = {"BULK", "SP_OACREATE"}
FORBIDDEN_PREFIXES = ("XP_", "SP_")

# SELECT dışı her ifade türü
FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop,
    exp.Alter, exp.Command, exp.Into, exp.TruncateTable, exp.Use, exp.Set,
    exp.Grant, exp.Transaction, exp.Commit, exp.Rollback, exp.Lock,
)


@dataclass
class GuardResult:
    ok: bool
    sql: str = ""
    error: Optional[str] = None
    tables: Optional[Set[str]] = None


def normalize_table_name(name: str) -> str:
    """'[dbo].[Order Details]' -> 'dbo.order details' (karşılaştırma için)."""
    parts = [p.strip().strip("[]\"`").lower() for p in re.split(r"\.", name or "")]
    return ".".join(p for p in parts if p)


def allowed_table_variants(tables: Iterable[str], default_schema: Optional[str] = "dbo") -> Set[str]:
    """Seçili tablolar için kabul edilen yazımlar: varsayılan şemada 'orders' ve 'dbo.orders' ikisi de geçerli."""
    allowed: Set[str] = set()
    prefix = f"{default_schema.lower()}." if default_schema else None
    for table in tables:
        norm = normalize_table_name(table)
        allowed.add(norm)
        if prefix and norm.startswith(prefix):
            allowed.add(norm[len(prefix):])
        elif prefix and "." not in norm:
            allowed.add(prefix + norm)
    return allowed


def _table_key(table: exp.Table, own_database: Optional[str] = None, schemaless: bool = False) -> str:
    """Tablo adının karşılaştırma anahtarı. Şemasız türlerde (MySQL, SQLite) kendi veritabanı öneki atılır."""
    if table.args.get("catalog") is not None:
        raise ValueError("Başka veritabanına başvuru (üç parçalı ad) yasak")
    db_part = table.args.get("db")
    if schemaless and db_part is not None and own_database and str(db_part.name).lower() == own_database.lower():
        db_part = None
    parts = [p for p in (db_part, table.this) if p is not None]
    return ".".join(str(getattr(p, "name", p)).lower() for p in parts)


def validate_sql(sql: str, allowed_tables: Optional[Iterable[str]] = None, dialect_id: str = "mssql",
                 database: Optional[str] = None) -> GuardResult:
    """SQL'i ilgili veritabanı lehçesiyle doğrular. Başarılıysa temizlenmiş tek ifadeyi döndürür."""
    dialect = get_dialect(dialect_id)
    forbidden = FORBIDDEN_FUNCTIONS | {f.upper() for f in dialect.forbidden_functions}
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        return GuardResult(False, error="Boş SQL üretildi")

    try:
        statements = [s for s in sqlglot.parse(text, read=dialect.sqlglot) if s is not None]
    except SqlglotError as exc:
        return GuardResult(False, text, f"SQL ayrıştırılamadı: {str(exc).splitlines()[0]}")

    if len(statements) != 1:
        return GuardResult(False, text, "Yalnızca tek bir SQL ifadesine izin verilir")
    root = statements[0]

    if not isinstance(root, (exp.Select, exp.Union, exp.Subquery)):
        return GuardResult(False, text, "Yalnızca SELECT sorgularına izin verilir")

    for node in root.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return GuardResult(False, text, f"Yasak SQL öğesi: {type(node).__name__.upper()}")
        if isinstance(node, (exp.Anonymous, exp.Func)):
            name = (node.name or "").upper() if isinstance(node, exp.Anonymous) else type(node).__name__.upper()
            if name in forbidden or name.startswith(FORBIDDEN_PREFIXES):
                return GuardResult(False, text, f"Yasak fonksiyon: {name}")

    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    used: Set[str] = set()
    for table in root.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            return GuardResult(False, text, "Tablo-değerli fonksiyonlara izin verilmez")
        try:
            key = _table_key(table, database, schemaless=dialect.default_schema is None)
        except ValueError as exc:
            return GuardResult(False, text, str(exc))
        if key in cte_names:
            continue
        used.add(key)

    if allowed_tables is not None:
        allowed = allowed_table_variants(allowed_tables, dialect.default_schema)
        outside = sorted(t for t in used if t not in allowed)
        if outside:
            return GuardResult(
                False, text,
                "Seçilmeyen tablolara erişilemez: " + ", ".join(outside),
            )

    return GuardResult(True, text, tables=used)
