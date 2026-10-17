"""
Uygulama yapılandırması.

Üç katman vardır:
  1. Sabitler (Config): kodda değişmeyen limitler.
  2. Kullanıcı ayarları (Settings): %APPDATA%/NIQ/settings.json, arayüzden değişir.
  3. Sırlar: API anahtarları OS keyring'inde tutulur, dosyaya veya exe'ye yazılmaz.
     Geliştirme kolaylığı için ortam değişkenleri / .env son çare olarak okunur.
"""
import json
import logging
import os
import secrets
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import keyring
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

APP_NAME = "NIQ"
KEYRING_SERVICE = os.getenv("KEYRING_SERVICE", "niq-desktop")

# .env yalnızca geliştirme içindir; dağıtılan exe'ye gömülmez.
if not getattr(sys, "frozen", False):
    load_dotenv()


def data_dir() -> Path:
    """Kullanıcıya özel veri klasörü (ayarlar, geçmiş, log). NIQ_HOME ile değiştirilebilir."""
    override = os.getenv("NIQ_HOME")
    if override:
        base = Path(override)
    else:
        root = os.getenv("APPDATA") or str(Path.home() / ".config")
        base = Path(root) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


class Config:
    """Değişmeyen uygulama sabitleri."""

    DB_CONNECTION_TIMEOUT: int = int(os.getenv("DB_CONNECTION_TIMEOUT", "5"))
    QUERY_TIMEOUT: int = int(os.getenv("QUERY_TIMEOUT", "30"))
    DEFAULT_ODBC_DRIVER: str = os.getenv("DEFAULT_ODBC_DRIVER", "ODBC Driver 18 for SQL Server")
    MAX_TABLES_PER_QUERY: int = int(os.getenv("MAX_TABLES_PER_QUERY", "10"))
    MAX_QUERY_LENGTH: int = int(os.getenv("MAX_QUERY_LENGTH", "1000"))
    SCHEMA_CACHE_TTL: int = 300
    # SQL hata verirse hatayı modele geri verip yeniden deneme sayısı
    REPAIR_ATTEMPTS: int = 2
    # Geçmişte saklanan en fazla sonuç satırı
    HISTORY_MAX_ROWS: int = 200

    @staticmethod
    def secret_key() -> str:
        """Flask oturum anahtarı; ilk çalıştırmada üretilip saklanır (restart'ta oturum düşmez)."""
        env = os.getenv("SECRET_KEY")
        if env:
            return env
        path = data_dir() / "secret.key"
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
            key = secrets.token_hex(32)
            path.write_text(key, encoding="utf-8")
            return key
        except OSError:
            return secrets.token_hex(32)


# Sağlayıcı sabitleri: ad, varsayılan base_url, ortam değişkeni, varsayılan model
PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "default_model": "openai/gpt-6-luna",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": None,
        "env_key": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
    },
    "local": {
        "label": "Yerel (Ollama / LM Studio)",
        "base_url": "http://127.0.0.1:11434/v1",
        "env_key": None,
        "default_model": "llama3.1",
    },
}

DEFAULT_SETTINGS: Dict[str, Any] = {
    "last_connection_id": "",
    "provider": "openrouter",
    "models": {name: p["default_model"] for name, p in PROVIDERS.items()},
    "local_base_url": PROVIDERS["local"]["base_url"],
    # Çıktı belirteci üst sınırı. Düşünen (reasoning) modeller görünmeyen
    # belirteçleri de bu limitten harcadığı için SQL için bile geniş tutulur.
    "max_output_tokens": 2048,
    "row_limit": 1000,
    # Sonuç satırlarını AI'a gönderip yorum üretme: varsayılan kapalı (veri gizliliği)
    "ai_analysis": False,
    # Az çeşitlilikli kolonların örnek değerlerini (durum kodları, ülkeler) AI istemine ekleme
    "ai_value_samples": False,
    # ODBC Driver 18 varsayılan olarak şifreler; yerel/öz imzalı sertifika için
    "trust_server_cert": True,
    # Bildirim kanalları (Telegram bot belirteci keyring'de, burada yalnızca sohbet kimliği)
    "notify": {"telegram_chat_id": "", "webhook_url": ""},
    # Sesli sorgu: konuşmayı metne çeviren model (OpenRouter/OpenAI)
    "stt_model": "google/gemini-3.5-flash-lite",
}


class Settings:
    """settings.json okuma/yazma ve sağlayıcı anahtar yönetimi."""

    _lock = threading.Lock()

    @classmethod
    def _path(cls) -> Path:
        return data_dir() / "settings.json"

    @classmethod
    def load(cls) -> Dict[str, Any]:
        merged = json.loads(json.dumps(DEFAULT_SETTINGS))
        try:
            stored = json.loads(cls._path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        for key, value in stored.items():
            if key == "models" and isinstance(value, dict):
                merged["models"].update({k: v for k, v in value.items() if isinstance(v, str) and v})
            elif key == "notify" and isinstance(value, dict):
                merged["notify"].update({k: v for k, v in value.items() if k in merged["notify"]})
            elif key in merged:
                merged[key] = value
        return merged

    @classmethod
    def save(cls, updates: Dict[str, Any]) -> Dict[str, Any]:
        with cls._lock:
            current = cls.load()
            for key, value in updates.items():
                if key == "models" and isinstance(value, dict):
                    current["models"].update(value)
                elif key == "notify" and isinstance(value, dict):
                    current["notify"].update({k: v for k, v in value.items() if k in current["notify"]})
                elif key in current:
                    current[key] = value
            cls._path().write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            return current

    # --- API anahtarları -------------------------------------------------
    @staticmethod
    def get_api_key(provider: str) -> Optional[str]:
        if provider == "local":
            return "local"  # SDK boş anahtarı kabul etmez; yerel sunucu yok sayar
        try:
            stored = keyring.get_password(KEYRING_SERVICE, f"ai:{provider}")
        except Exception as exc:  # keyring backend yoksa
            logger.warning("Keyring okunamadı: %s", exc)
            stored = None
        if stored:
            return stored
        env_name = PROVIDERS.get(provider, {}).get("env_key")
        return os.getenv(env_name) if env_name else None

    @staticmethod
    def set_api_key(provider: str, api_key: str) -> None:
        keyring.set_password(KEYRING_SERVICE, f"ai:{provider}", api_key)

    @staticmethod
    def delete_api_key(provider: str) -> None:
        try:
            keyring.delete_password(KEYRING_SERVICE, f"ai:{provider}")
        except keyring.errors.PasswordDeleteError:
            pass

    @classmethod
    def has_api_key(cls, provider: str) -> bool:
        return bool(cls.get_api_key(provider))

    # --- AI istemci ayarı -------------------------------------------------
    @classmethod
    def ai_config(cls) -> Dict[str, Any]:
        """Aktif sağlayıcı için SDK ayarları. Anahtar yoksa api_key None döner."""
        settings = cls.load()
        provider = settings["provider"] if settings["provider"] in PROVIDERS else "openrouter"
        base_url = PROVIDERS[provider]["base_url"]
        if provider == "local":
            base_url = settings.get("local_base_url") or base_url
        return {
            "provider": provider,
            "api_key": cls.get_api_key(provider),
            "base_url": base_url,
            "model": settings["models"].get(provider) or PROVIDERS[provider]["default_model"],
            "max_output_tokens": int(settings["max_output_tokens"]),
        }
