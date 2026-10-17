"""Named AI accounts. Public metadata on disk; credentials only in the OS vault.

No credential pooling, automatic billing fallback, or imports from another app.
File locks serialize metadata and rotating refresh tokens across desktop/MCP.
"""
import json
import os
import time
import uuid
from contextlib import nullcontext
from urllib.parse import urlsplit, urlunsplit

import keyring
from filelock import FileLock

from backend.config.config import KEYRING_SERVICE, Settings, data_dir
from backend.core.utils import ValidationError

ACCOUNT_PROVIDERS = {
    "chatgpt": {"label": "ChatGPT", "auth": "oauth", "billing": "ChatGPT planı", "voice": False},
    "openai": {"label": "OpenAI", "url": "https://api.openai.com/v1", "voice": True, "simple": True, "default_model": "gpt-6-luna", "icon": "chatgpt-logo-white.svg"},
    "anthropic": {"label": "Anthropic", "url": "https://api.anthropic.com", "voice": False, "simple": True, "default_model": "claude-haiku-5.5", "icon": "anthropic.ico"},
    "openrouter": {"label": "OpenRouter", "url": "https://openrouter.ai/api/v1", "voice": True, "simple": True, "default_model": "openai/gpt-6-luna", "icon": "openrouter.png"},
    "gemini": {"label": "Gemini", "url": "https://generativelanguage.googleapis.com/v1beta/openai", "voice": False, "simple": True, "default_model": "gemini-3.8-flash", "icon": "gemini.svg"},
    "groq": {"label": "Groq · Llama", "url": "https://api.groq.com/openai/v1", "voice": False, "simple": True, "default_model": "llama-3.3-70b-versatile", "icon": "groq.ico"},
    "mistral": {"label": "Mistral", "url": "https://api.mistral.ai/v1", "voice": False, "simple": True, "default_model": "mistral-small-latest", "icon": "mistral.png"},
    "deepseek": {"label": "DeepSeek", "url": "https://api.deepseek.com", "voice": False, "simple": True, "default_model": "deepseek-flash", "icon": "deepseek.svg"},
    "azure": {"label": "Azure OpenAI", "voice": False},
    "compatible": {"label": "OpenAI uyumlu", "voice": False},
    "local": {"label": "Yerel model", "url": "http://127.0.0.1:11434/v1", "voice": False},
}


def endpoint(provider, value=""):
    fixed = ACCOUNT_PROVIDERS[provider].get("url")
    if fixed and provider != "local":
        return fixed
    value = str(value or fixed or "").strip()
    parsed = urlsplit(value)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.scheme not in ("http", "https")):
        raise ValidationError("Geçerli, anahtar içermeyen bir sunucu adresi gir.")
    if parsed.scheme != "https" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValidationError("Uzak AI sunucusu HTTPS kullanmalı.")
    if provider == "azure":
        if parsed.scheme != "https" or not parsed.hostname.endswith((".openai.azure.com", ".services.ai.azure.com")):
            raise ValidationError("Azure kaynağının HTTPS adresini gir.")
        if parsed.path.rstrip("/") not in ("", "/openai/v1"):
            raise ValidationError("Azure kaynak adresi veya /openai/v1 adresi gerekli.")
        parsed = parsed._replace(path="/openai/v1")
    return urlunsplit(parsed).rstrip("/")


# Corporate administrators provision the Azure resource once; end users only
# choose a provider, name and key. An API key cannot reveal its Azure resource.
if os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_DEPLOYMENT"):
    try:
        azure_url = endpoint("azure", os.environ["AZURE_OPENAI_ENDPOINT"])
        ACCOUNT_PROVIDERS["azure"].update(simple=True, url=azure_url,
                                        default_model=os.environ["AZURE_OPENAI_DEPLOYMENT"], icon="microsoft.svg")
    except ValidationError:
        pass


class AccountStore:
    @staticmethod
    def _chunks(name, value):
        generation = value.get("_niq_generation")
        return [name + ":" + generation + ":" + str(i) for i in range(value.get("count", 0))] if generation else []

    def lock(self, name="accounts"):
        return FileLock(str(data_dir() / ("ai-" + name + ".lock")), timeout=30)

    def _read(self):
        try:
            return json.loads((data_dir() / "ai-accounts.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"accounts": [], "host_id": ""}

    def _write(self, data):
        path = data_dir() / "ai-accounts.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def host_id(self):
        with self.lock():
            data = self._read()
            if not data["host_id"]:
                data["host_id"] = "urn:uuid:" + str(uuid.uuid4())
                self._write(data)
            return data["host_id"]

    def list(self):
        with self.lock():
            return self._read()["accounts"]

    def get(self, account_id):
        item = next((a for a in self.list() if a["id"] == account_id), None)
        if not item:
            raise ValidationError("AI hesabı bulunamadı.")
        return item

    def secret(self, account_id):
        self.get(account_id)
        try:
            raw = keyring.get_password(KEYRING_SERVICE, "ai-account:" + account_id)
            value = json.loads(raw) if raw else {}
            if "_niq_generation" in value:
                parts = [keyring.get_password(KEYRING_SERVICE, name) for name in self._chunks("ai-account:" + account_id, value)]
                if any(part is None for part in parts):
                    raise ValueError("incomplete credential")
                value = json.loads("".join(parts))
            return value
        except Exception:
            raise ValidationError("İşletim sistemi anahtar kasası okunamadı.") from None

    def set_secret(self, account_id, value):
        name = "ai-account:" + account_id
        written = []
        try:
            old_raw = keyring.get_password(KEYRING_SERVICE, name)
            old = json.loads(old_raw) if old_raw else {}
            raw = json.dumps(value, ensure_ascii=True)
            # Windows generic credentials allow only 2560 bytes (UTF-16).
            # Write a fresh generation, then atomically publish its small pointer.
            if len(raw) > 1000:
                generation = str(uuid.uuid4())
                for index, offset in enumerate(range(0, len(raw), 1000)):
                    chunk = name + ":" + generation + ":" + str(index)
                    keyring.set_password(KEYRING_SERVICE, chunk, raw[offset:offset + 1000])
                    written.append(chunk)
                raw = json.dumps({"_niq_generation": generation, "count": len(written)})
            keyring.set_password(KEYRING_SERVICE, name, raw)
        except Exception:
            for chunk in written:
                try:
                    keyring.delete_password(KEYRING_SERVICE, chunk)
                except Exception:
                    pass
            raise ValidationError("Giriş bilgileri işletim sistemi anahtar kasasına kaydedilemedi.") from None
        for chunk in self._chunks(name, old):
            try:
                keyring.delete_password(KEYRING_SERVICE, chunk)
            except Exception:
                pass

    def put(self, item, secret=None):
        with (self.lock("refresh-" + item["id"]) if secret is not None else nullcontext()), self.lock():
            data = self._read()
            if secret is not None:
                self.set_secret(item["id"], secret)
            data["accounts"] = [a for a in data["accounts"] if a["id"] != item["id"]] + [item]
            self._write(data)
        return item

    def add_key(self, data):
        provider = data.get("provider")
        if provider not in ACCOUNT_PROVIDERS or provider == "chatgpt":
            raise ValidationError("Bu sağlayıcı için tarayıcıdan giriş gerekli.")
        model = str(data.get("model") or "").strip()[:120]
        if not model:
            raise ValidationError("Model veya Azure deployment adı gerekli.")
        key = str(data.get("api_key") or "").strip()
        if not key and provider != "local":
            raise ValidationError("API anahtarını gir.")
        item = {"id": str(uuid.uuid4()), "provider": provider, "auth": "api_key",
                "label": str(data.get("label") or ACCOUNT_PROVIDERS[provider]["label"]).strip()[:80],
                "model": model, "base_url": endpoint(provider, data.get("base_url")), "connected": True}
        return self.put(item, {"api_key": key or "local"})

    def configure(self, account_id, refresh=True):
        item = self.get(account_id)
        if not item.get("connected"):
            raise ValidationError("Seçili hesaba yeniden giriş yap.")
        if item["provider"] == "chatgpt":
            secret = self.chatgpt_credentials(account_id) if refresh else self.secret(account_id)
            if "chatgpt.tokens.use.direct" not in secret.get("scope", "").split():
                raise ValidationError("ChatGPT plan kullanımına izin verilmedi.")
            key = secret["access_token"]
        else:
            key = self.secret(account_id).get("api_key")
        return {**item, "account_id": item["id"], "api_key": key,
                "max_output_tokens": int(Settings.load()["max_output_tokens"])}

    def chatgpt_credentials(self, account_id):
        from backend.services.ai_auth import refresh_chatgpt
        with self.lock("refresh-" + account_id):
            item = self.get(account_id)
            secret = self.secret(account_id)
            if "chatgpt.tokens.use.direct" not in secret.get("scope", "").split():
                raise ValidationError("ChatGPT plan kullanımına izin verilmedi. Yeniden giriş yap.")
            if secret.get("expires_at", 0) < time.time() + 90:
                secret = refresh_chatgpt(item, secret)
                self.set_secret(account_id, secret)
            return secret

    def disconnect(self, account_id):
        from backend.services.ai_auth import revoke_chatgpt
        with self.lock("refresh-" + account_id):
            item = self.get(account_id)
            revoked = True
            if item["provider"] == "chatgpt":
                revoked = revoke_chatgpt(item, self.secret(account_id))
            try:
                raw = keyring.get_password(KEYRING_SERVICE, "ai-account:" + account_id)
                for chunk in self._chunks("ai-account:" + account_id, json.loads(raw) if raw else {}):
                    keyring.delete_password(KEYRING_SERVICE, chunk)
                keyring.delete_password(KEYRING_SERVICE, "ai-account:" + account_id)
            except keyring.errors.PasswordDeleteError:
                pass
            self.put({**item, "connected": False})
        return revoked


def selected_config(kind="chat", refresh=True):
    settings = Settings.load()
    account_id = settings.get("voice_account_id" if kind == "voice" else "ai_account_id")
    if account_id:
        config = AccountStore().configure(account_id, refresh=refresh)
        if kind == "voice" and not ACCOUNT_PROVIDERS[config["provider"]]["voice"]:
            raise ValidationError("Ses için OpenAI API veya OpenRouter hesabı seç.")
        return config
    if kind == "voice" and settings.get("ai_account_id"):
        raise ValidationError("Sohbet hesabından ayrı bir ses hesabı seç. Ses kullanımı API bakiyesinden düşer.")
    return Settings.ai_config()
