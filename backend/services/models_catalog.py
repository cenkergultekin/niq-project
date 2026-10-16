"""
Model seçici için katalog. Kürate liste her zaman döner; OpenRouter'ın herkese
açık /models listesi ulaşılabiliyorsa fiyat bilgisi eklenir (1 saat önbellek).
"""
import logging
import threading
import time
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)

CURATED: Dict[str, List[Dict[str, str]]] = {
    "openrouter": [
        {"id": "openai/gpt-6-luna", "name": "GPT-6 Luna (varsayılan, hızlı ve ucuz)"},
        {"id": "openai/gpt-6-luna-pro", "name": "GPT-6 Luna Pro (daha güçlü)"},
        {"id": "anthropic/claude-haiku-5.5", "name": "Claude Haiku 5.5"},
        {"id": "openai/gpt-4o-mini", "name": "GPT-4o mini"},
    ],
    "openai": [
        {"id": "gpt-4o-mini", "name": "GPT-4o mini"},
        {"id": "gpt-4o", "name": "GPT-4o"},
    ],
    "local": [
        {"id": "llama3.1", "name": "Llama 3.1 (Ollama)"},
        {"id": "qwen2.5-coder", "name": "Qwen 2.5 Coder (Ollama)"},
    ],
}

_cache: Dict[str, Any] = {"at": 0.0, "prices": {}}
_lock = threading.Lock()


def _openrouter_prices() -> Dict[str, Dict[str, float]]:
    """{model_id: {input, output}} (1M belirteç başına USD). Hata olursa son bilinen/boş."""
    with _lock:
        if time.time() - _cache["at"] < 3600 and _cache["prices"]:
            return _cache["prices"]
        try:
            data = requests.get("https://openrouter.ai/api/v1/models", timeout=4).json().get("data", [])
            prices = {}
            for item in data:
                pricing = item.get("pricing") or {}
                try:
                    prices[item["id"]] = {
                        "input": round(float(pricing.get("prompt", 0)) * 1_000_000, 3),
                        "output": round(float(pricing.get("completion", 0)) * 1_000_000, 3),
                    }
                except (TypeError, ValueError):
                    continue
            _cache.update(at=time.time(), prices=prices)
        except Exception as exc:
            logger.info("OpenRouter fiyat listesi alınamadı: %s", exc)
            _cache["at"] = time.time() - 3300  # 5 dk sonra tekrar dene
        return _cache["prices"]


def list_models(provider: str) -> List[Dict[str, Any]]:
    models = [dict(m) for m in CURATED.get(provider, [])]
    if provider == "openrouter":
        prices = _openrouter_prices()
        for model in models:
            if model["id"] in prices:
                model["price"] = prices[model["id"]]
    return models
