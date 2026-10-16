"""Sonuçları CSV (Excel'de Türkçe karakterli açılır) ve XLSX olarak dışa aktarır."""
import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List


def _cell(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return value


def _headers(rows: List[Dict[str, Any]]) -> List[str]:
    return list(rows[0].keys()) if rows else []


def to_csv(rows: List[Dict[str, Any]]) -> bytes:
    """UTF-8 BOM'lu, noktalı virgül ayraçlı CSV (Türkçe Excel'in beklediği biçim)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    headers = _headers(rows)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_safe(_cell(row.get(h))) for h in headers])
    return buffer.getvalue().encode("utf-8-sig")


def _safe(value: Any) -> Any:
    """CSV/Excel formül enjeksiyonunu önler: = + - @ ile başlayan metinlerin başına ' eklenir."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def to_xlsx(rows: List[Dict[str, Any]], sheet_name: str = "Sonuç") -> bytes:
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name[:31]
    headers = _headers(rows)
    sheet.append(headers)
    for row in rows:
        sheet.append([_safe(_cell(row.get(h))) for h in headers])
    for index, header in enumerate(headers, start=1):
        width = max([len(str(header))] + [len(str(_cell(r.get(header)) or "")) for r in rows[:200]])
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = min(max(width + 2, 8), 60)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
