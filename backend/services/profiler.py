"""
Veri profili: az çeşitlilikli kolonların (durum kodu, ülke, kategori) gerçek değerlerini yerelde çıkarır.
Model böylece `Country = 'Germany'` mi `'Almanya'` mı, `status = 'A'` mı bilir.
Kişisel veri gibi görünen kolonlar atlanır; değerler AI'a yalnızca kullanıcı izin verirse gider.
"""
import logging
from typing import Any, Dict, List, Optional

from backend.db.connector import Connector
from backend.services.knowledge import PII_COLUMN, KnowledgeStore

logger = logging.getLogger(__name__)

SAMPLE_ROWS = 1000
MAX_DISTINCT = 12
MAX_VALUE_LENGTH = 40
ID_LIKE = ("id", "no", "kod", "code")


def sample_sql(connector: Connector, table: str, rows: int) -> str:
    quoted = connector.quote_table(table)
    if connector.profile.type == "mssql":
        return f"SELECT TOP {rows} * FROM {quoted}"
    return f"SELECT * FROM {quoted} LIMIT {rows}"


def categorical_values(rows: List[Dict[str, Any]], max_distinct: int = MAX_DISTINCT) -> Dict[str, List[Any]]:
    """Örnek satırlardan az çeşitlilikli kolonları ve değerlerini çıkarır."""
    if not rows:
        return {}
    result: Dict[str, List[Any]] = {}
    for column in rows[0].keys():
        if PII_COLUMN.search(str(column)):
            continue
        values = {row[column] for row in rows if row[column] is not None}
        if not values or len(values) > max_distinct:
            continue
        if all(isinstance(v, str) for v in values):
            if any(len(v) > MAX_VALUE_LENGTH for v in values):
                continue
        elif all(isinstance(v, (int, bool)) for v in values):
            if str(column).lower().endswith(ID_LIKE) and len(values) > 3:
                continue
        else:
            continue  # tarih, ondalık, ikili veri: kategori değil
        if len(rows) >= 20 and len(values) > len(rows) * 0.5:
            continue  # hemen hemen her satır farklı: kategori değil
        result[str(column)] = sorted(values, key=lambda v: (str(type(v)), v))
    return result


def profile_database(connector: Connector, store: KnowledgeStore, tables: Optional[List[str]] = None,
                     max_tables: int = 40) -> Dict[str, Any]:
    """Tabloları örnekleyip profili kaydeder. Salt-okunur çalışır; hatalı tablo atlanır."""
    names = tables if tables is not None else connector.get_tables()[:max_tables]
    profiled, skipped, columns = 0, 0, 0
    for table in names:
        response = connector.execute_readonly(sample_sql(connector, table, SAMPLE_ROWS), SAMPLE_ROWS)
        if not response.is_successful:
            skipped += 1
            logger.info("Profil atlandı (%s): %s", table, response.error)
            continue
        values = categorical_values(response.results or [])
        store.save_profile(connector.db_key, table, values)
        profiled += 1
        columns += len(values)
    return {"tables": profiled, "skipped": skipped, "columns": columns}
