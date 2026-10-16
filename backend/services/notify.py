"""
Bildirim kanalları: uygulama içi gelen kutusu (her zaman), Telegram ve webhook.

Telegram bot belirteci OS keyring'inde tutulur; sohbet kimliği ve webhook adresi ayarlardadır.
"""
import json
import logging
from typing import Any, Dict, List, Optional

import keyring
import requests

from backend.config.config import KEYRING_SERVICE, Settings
from backend.services.exporter import to_csv

logger = logging.getLogger(__name__)

TELEGRAM_LIMIT = 3800
CHANNELS = ("inbox", "telegram", "webhook")


def get_telegram_token() -> Optional[str]:
    try:
        return keyring.get_password(KEYRING_SERVICE, "notify:telegram_token")
    except Exception:
        return None


def set_telegram_token(token: str) -> None:
    keyring.set_password(KEYRING_SERVICE, "notify:telegram_token", token)


def delete_telegram_token() -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, "notify:telegram_token")
    except keyring.errors.PasswordDeleteError:
        pass


def format_rows(rows: List[Dict[str, Any]], limit: int = 10) -> str:
    """Sonucu mesaja sığacak düz metin tablo olarak biçimler."""
    if not rows:
        return "(sonuç boş)"
    headers = list(rows[0].keys())
    lines = [" | ".join(headers)]
    for row in rows[:limit]:
        lines.append(" | ".join(str(row.get(h, ""))[:30] for h in headers))
    if len(rows) > limit:
        lines.append(f"... ve {len(rows) - limit} satır daha")
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str, csv_bytes: Optional[bytes] = None, filename: str = "rapor.csv") -> None:
    base = f"https://api.telegram.org/bot{token}"
    response = requests.post(f"{base}/sendMessage", json={"chat_id": chat_id, "text": text[:TELEGRAM_LIMIT]}, timeout=15)
    response.raise_for_status()
    if csv_bytes:
        files = {"document": (filename, csv_bytes, "text/csv")}
        requests.post(f"{base}/sendDocument", data={"chat_id": chat_id}, files=files, timeout=30).raise_for_status()


def send_webhook(url: str, payload: Dict[str, Any]) -> None:
    requests.post(url, data=json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
                  headers={"Content-Type": "application/json; charset=utf-8"}, timeout=15).raise_for_status()


def notify(channels: List[str], title: str, text: str, rows: Optional[List[Dict[str, Any]]] = None,
           payload: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Seçili kanallara gönderir. Dönüş: kanal -> 'ok' veya hata metni (gelen kutusu her zaman çağıran tarafça yazılır)."""
    settings = Settings.load().get("notify", {})
    results: Dict[str, str] = {}
    body = f"{title}\n\n{text}"
    if rows:
        body += "\n\n" + format_rows(rows)
    if "telegram" in channels:
        token, chat_id = get_telegram_token(), settings.get("telegram_chat_id")
        if not token or not chat_id:
            results["telegram"] = "Telegram yapılandırılmamış (Ayarlar > Bildirimler)"
        else:
            try:
                send_telegram(token, str(chat_id), body, to_csv(rows) if rows and len(rows) > 10 else None)
                results["telegram"] = "ok"
            except Exception as exc:
                results["telegram"] = f"Telegram gönderilemedi: {type(exc).__name__}"
                logger.warning("Telegram hatası: %s", type(exc).__name__)  # belirteç içerebilecek ayrıntı loglanmaz
    if "webhook" in channels:
        url = settings.get("webhook_url")
        if not url:
            results["webhook"] = "Webhook adresi yok (Ayarlar > Bildirimler)"
        else:
            try:
                send_webhook(url, {"title": title, "text": text, "rows": (rows or [])[:50], **(payload or {})})
                results["webhook"] = "ok"
            except Exception as exc:
                results["webhook"] = f"Webhook gönderilemedi: {type(exc).__name__}"
    return results
