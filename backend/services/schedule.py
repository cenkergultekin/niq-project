"""
Zamanlama tanımları: sonraki çalışma zamanı, Türkçe/İngilizce metinden ayrıştırma, uyarı koşulları.
Saatler bilgisayarın yerel saatidir.
"""
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

DAY_NAMES = {
    "pazartesi": 0, "sali": 1, "salı": 1, "carsamba": 2, "çarşamba": 2, "persembe": 3, "perşembe": 3,
    "cuma": 4, "cumartesi": 5, "pazar": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}
DAY_LABELS = ["pazartesi", "salı", "çarşamba", "perşembe", "cuma", "cumartesi", "pazar"]


def _parse_time(text: str) -> Optional[str]:
    match = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", text) or re.search(r"\bsaat\s*([01]?\d|2[0-3])\b", text) \
        or re.search(r"\b([01]?\d|2[0-3])\s*(?:de|da|te|ta)\b", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2)) if match.lastindex and match.lastindex >= 2 else 0
    return f"{hour:02d}:{minute:02d}"


def validate_schedule(schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Yapısal zamanlamayı doğrular ve normalize eder; hatalıysa ValueError."""
    kind = schedule.get("kind")
    clean: Dict[str, Any] = {"kind": kind}
    if kind == "interval":
        minutes = int(schedule.get("minutes", 0))
        if not 5 <= minutes <= 60 * 24 * 7:
            raise ValueError("Aralık 5 dakika ile 7 gün arasında olmalı")
        clean["minutes"] = minutes
        return clean
    if kind in ("daily", "weekly", "monthly"):
        at = str(schedule.get("time", "09:00"))
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at):
            raise ValueError("Saat HH:MM biçiminde olmalı")
        clean["time"] = at
        if kind == "weekly":
            day = int(schedule.get("day", 0))
            if not 0 <= day <= 6:
                raise ValueError("Gün 0 (pazartesi) ile 6 (pazar) arasında olmalı")
            clean["day"] = day
        if kind == "monthly":
            day = int(schedule.get("day", 1))
            if not 1 <= day <= 28:
                raise ValueError("Ayın günü 1 ile 28 arasında olmalı")
            clean["day"] = day
        return clean
    raise ValueError("Bilinmeyen zamanlama türü")


def next_run(schedule: Dict[str, Any], after: datetime) -> datetime:
    """`after` anından sonraki ilk çalışma zamanı."""
    kind = schedule["kind"]
    if kind == "interval":
        return after + timedelta(minutes=int(schedule["minutes"]))
    hour, minute = (int(x) for x in schedule.get("time", "09:00").split(":"))
    candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if kind == "daily":
        return candidate if candidate > after else candidate + timedelta(days=1)
    if kind == "weekly":
        ahead = (int(schedule["day"]) - candidate.weekday()) % 7
        candidate += timedelta(days=ahead)
        return candidate if candidate > after else candidate + timedelta(days=7)
    if kind == "monthly":
        day = int(schedule["day"])
        candidate = candidate.replace(day=day)
        if candidate > after:
            return candidate
        year, month = (candidate.year + 1, 1) if candidate.month == 12 else (candidate.year, candidate.month + 1)
        return candidate.replace(year=year, month=month, day=day)
    raise ValueError("Bilinmeyen zamanlama türü")


def describe_schedule(schedule: Dict[str, Any]) -> str:
    kind = schedule["kind"]
    if kind == "interval":
        minutes = schedule["minutes"]
        if minutes % 60 == 0:
            return f"her {minutes // 60} saatte bir" if minutes > 60 else "her saat"
        return f"her {minutes} dakikada bir"
    if kind == "daily":
        return f"her gün {schedule['time']}"
    if kind == "weekly":
        return f"her {DAY_LABELS[schedule['day']]} {schedule['time']}"
    return f"her ayın {schedule['day']}. günü {schedule['time']}"


def parse_schedule(text: str) -> Optional[Dict[str, Any]]:
    """'her pazartesi 09:00', 'her gün 8:30', 'her 30 dakikada', 'her saat', 'haftalık', 'aylık' gibi metinleri çözer."""
    lowered = (text or "").lower().strip()
    if not lowered:
        return None
    match = re.search(r"her\s+(\d+)\s*(dakika|dk|saat)", lowered) or re.search(r"every\s+(\d+)\s*(minute|min|hour)", lowered)
    if match:
        amount = int(match.group(1))
        minutes = amount * (60 if match.group(2) in ("saat", "hour") else 1)
        return validate_schedule({"kind": "interval", "minutes": minutes})
    if re.search(r"\bher\s+saat\b|\bhourly\b|\bsaatlik\b", lowered):
        return {"kind": "interval", "minutes": 60}
    at = _parse_time(lowered) or "09:00"
    for name, index in DAY_NAMES.items():
        if re.search(rf"\b{name}", lowered):
            return {"kind": "weekly", "day": index, "time": at}
    if re.search(r"haftal[ıi]k|weekly|her hafta", lowered):
        return {"kind": "weekly", "day": 0, "time": at}
    month_day = re.search(r"ay[ıi]n\s+(\d{1,2})", lowered)
    if month_day and 1 <= int(month_day.group(1)) <= 28:
        return {"kind": "monthly", "day": int(month_day.group(1)), "time": at}
    if re.search(r"ayl[ıi]k|monthly|her ay\b", lowered):
        return {"kind": "monthly", "day": 1, "time": at}
    if re.search(r"her\s*g[uü]n|g[uü]nl[uü]k|daily|every day|sabah|ak[sş]am", lowered):
        return {"kind": "daily", "time": at if at != "09:00" or not re.search(r"ak[sş]am", lowered) else "18:00"}
    return None


# --- uyarı koşulu --------------------------------------------------------------------
OPERATORS = {">", "<", ">=", "<=", "==", "!=", "rows>0", "rows==0"}


def validate_condition(condition: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not condition:
        return None
    op = condition.get("op")
    if op not in OPERATORS:
        raise ValueError(f"Koşul işleci geçersiz: {op}")
    clean: Dict[str, Any] = {"op": op}
    if not op.startswith("rows"):
        try:
            clean["value"] = float(condition["value"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Koşul için sayısal bir değer gerekli") from None
        if condition.get("column"):
            clean["column"] = str(condition["column"])
    return clean


def evaluate_condition(condition: Optional[Dict[str, Any]], rows: list) -> Dict[str, Any]:
    """Sonuç satırlarına koşulu uygular. Dönüş: {fired, observed}."""
    if not condition:
        return {"fired": True, "observed": None}
    op = condition["op"]
    if op == "rows>0":
        return {"fired": len(rows) > 0, "observed": len(rows)}
    if op == "rows==0":
        return {"fired": len(rows) == 0, "observed": len(rows)}
    if not rows:
        return {"fired": False, "observed": None}
    first = rows[0]
    column = condition.get("column")
    value = first.get(column) if column and column in first else next(
        (v for v in first.values() if isinstance(v, (int, float)) and not isinstance(v, bool)), None)
    try:
        observed = float(value)
    except (TypeError, ValueError):
        return {"fired": False, "observed": value}
    threshold = float(condition["value"])
    compare = {">": observed > threshold, "<": observed < threshold, ">=": observed >= threshold,
               "<=": observed <= threshold, "==": observed == threshold, "!=": observed != threshold}
    return {"fired": compare[op], "observed": observed}


def describe_condition(condition: Optional[Dict[str, Any]]) -> str:
    if not condition:
        return ""
    op = condition["op"]
    if op == "rows>0":
        return "sonuç satır döndürürse"
    if op == "rows==0":
        return "sonuç boş gelirse"
    target = condition.get("column") or "değer"
    return f"{target} {op} {condition['value']:g} ise"
