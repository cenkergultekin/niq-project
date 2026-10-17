"""Official browser sign-in, PKCE + OIDC; never another application's client ID."""
import base64
import hashlib
import secrets
import threading
import time
import uuid
import webbrowser
from urllib.parse import urlencode, urlsplit

import jwt
import requests

from backend.core.utils import ValidationError
from backend.services.ai_accounts import AccountStore

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
JWKS = jwt.PyJWKClient(ISSUER + "/.well-known/jwks.json", timeout=15)


def _post(url, **kwargs):
    try:
        response = requests.post(url, timeout=25, allow_redirects=False, **kwargs)
        if not 200 <= response.status_code < 300:
            raise ValidationError("Sağlayıcı girişi tamamlamadı. Yeniden giriş yap.")
        return response.json()
    except (requests.RequestException, ValueError):
        raise ValidationError("Giriş sunucusuna ulaşılamadı. Yeniden dene.") from None


def refresh_chatgpt(item, previous):
    if not previous.get("refresh_token"):
        raise ValidationError("ChatGPT oturumu sona erdi. Yeniden giriş yap.")
    result = _post(TOKEN_URL, data={"grant_type": "refresh_token", "client_id": item["client_id"],
                                   "refresh_token": previous["refresh_token"], "resource": RESOURCE})
    if not result.get("access_token"):
        raise ValidationError("ChatGPT oturumu yenilenemedi. Yeniden giriş yap.")
    updated = {**previous, **result, "expires_at": time.time() + int(result.get("expires_in", 3600))}
    if "chatgpt.tokens.use.direct" not in updated.get("scope", "").split():
        raise ValidationError("ChatGPT plan kullanım izni kaldırılmış. Yeniden giriş yap.")
    return updated


def revoke_chatgpt(item, secret):
    token = secret.get("refresh_token")
    if not token:
        return True
    try:
        response = requests.post(ISSUER + "/api/accounts/oauth/revoke", timeout=15, allow_redirects=False,
                                 data={"token": token, "token_type_hint": "refresh_token", "client_id": item["client_id"]})
        return response.status_code == 200
    except requests.RequestException:
        return False


class BrowserAuth:
    def __init__(self):
        self._lock = threading.Lock()
        self._pending = {}

    def start(self, provider, redirect_uri, account_id=""):
        if provider not in ("chatgpt", "openrouter"):
            raise ValidationError("Bu sağlayıcı tarayıcıdan giriş desteklemiyor.")
        parsed = urlsplit(redirect_uri)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path != "/auth/callback":
            raise ValidationError("Giriş yalnızca 127.0.0.1 masaüstü adresinde kullanılabilir.")
        store = AccountStore()
        item = store.get(account_id) if account_id else None
        if item and (item["provider"] != provider or provider != "chatgpt"):
            raise ValidationError("Giriş hesabı sağlayıcıyla eşleşmiyor.")
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(32)
        pending = {"provider": provider, "verifier": verifier, "nonce": secrets.token_urlsafe(32),
                   "redirect_uri": redirect_uri, "expires": time.time() + 600, "item": item,
                   "status": "pending", "message": "Tarayıcıda girişini tamamla."}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        params = {"state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        if provider == "chatgpt":
            params.update(client_id=item["client_id"] if item else "dynamic_agent_client",
                          ext_agent_host_id=store.host_id(), response_type="code", redirect_uri=redirect_uri,
                          scope=SCOPES, resource=RESOURCE, nonce=pending["nonce"])
            if not item:
                params["agent_name_hint"] = "NIQ"
            url = ISSUER + "/api/accounts/authorize?" + urlencode(params)
        else:
            params.update(callback_url=redirect_uri, key_label="NIQ")
            url = "https://openrouter.ai/auth?" + urlencode(params)
        with self._lock:
            self._pending = {s: p for s, p in self._pending.items() if p["expires"] > time.time()}
            self._pending[state] = pending
        if not webbrowser.open(url):
            with self._lock:
                pending.update(status="failed", message="Varsayılan tarayıcı başlatılamadı.")
            raise ValidationError(pending["message"])
        return {"attempt": state}

    def status(self, state):
        with self._lock:
            p = self._pending.get(state)
            if not p:
                raise ValidationError("Giriş isteği bulunamadı.")
            if p["status"] == "pending" and p["expires"] <= time.time():
                p.update(status="failed", message="Giriş süresi doldu. Yeniden dene.")
            return {k: p[k] for k in ("status", "message", "account_id") if k in p}

    def cancel(self, state):
        with self._lock:
            p = self._pending.get(state)
            if p and p["status"] == "pending":
                p.update(status="cancelled", message="Giriş iptal edildi.")

    def finish(self, params):
        state = params.get("state", "")
        with self._lock:
            p = self._pending.get(state)
            if not p or p["status"] != "pending" or p["expires"] <= time.time():
                raise ValidationError("Giriş isteği geçersiz veya süresi dolmuş.")
            p["status"] = "exchanging"  # single-use, including failure
        try:
            if params.get("error") or not params.get("code"):
                raise ValidationError("Giriş izni verilmedi. NIQ'ye dönerek yeniden deneyebilirsin.")
            item = self._exchange(p, params)
            p.update(status="completed", message="Giriş tamamlandı. NIQ'ye dönebilirsin.", account_id=item["id"])
            return p["message"]
        except ValidationError as exc:
            p.update(status="failed", message=str(exc))
            raise
        except Exception:
            p.update(status="failed", message="Giriş doğrulanamadı. NIQ'ye dönerek yeniden dene.")
            raise ValidationError(p["message"]) from None
        finally:
            p.pop("verifier", None)
            p.pop("nonce", None)

    def _exchange(self, p, params):
        store = AccountStore()
        if p["provider"] == "openrouter":
            result = _post("https://openrouter.ai/api/v1/auth/keys", json={"code": params["code"],
                           "code_verifier": p["verifier"], "code_challenge_method": "S256"})
            if not result.get("key"):
                raise ValidationError("OpenRouter anahtar döndürmedi.")
            return store.add_key({"provider": "openrouter", "label": "OpenRouter", "model": "openai/gpt-6-luna",
                                  "api_key": result["key"]})
        old = p["item"]
        client_id = params.get("client_id") or (old["client_id"] if old else "")
        if not client_id or client_id == "dynamic_agent_client" or (old and client_id != old["client_id"]):
            raise ValidationError("ChatGPT kayıt kimliği eşleşmedi.")
        result = _post(TOKEN_URL, data={"grant_type": "authorization_code", "client_id": client_id,
                       "code": params["code"], "code_verifier": p["verifier"],
                       "redirect_uri": p["redirect_uri"], "resource": RESOURCE})
        token = result.get("id_token", "")
        signing_key = JWKS.get_signing_key_from_jwt(token)
        identity = jwt.decode(token, signing_key.key, algorithms=["RS256"], audience=client_id, issuer=ISSUER,
                              options={"require": ["exp", "iss", "aud", "sub", "nonce"]})
        if not secrets.compare_digest(str(identity["nonce"]), p["nonce"]):
            raise ValidationError("ChatGPT giriş kimliği eşleşmedi.")
        if old and identity["sub"] != old["subject"]:
            raise ValidationError("Seçilen ChatGPT hesabı eşleşmedi.")
        if not result.get("access_token") or "chatgpt.tokens.use.direct" not in result.get("scope", "").split():
            raise ValidationError("ChatGPT plan kullanımına izin verilmedi.")
        item = {"id": old["id"] if old else str(uuid.uuid4()), "provider": "chatgpt", "auth": "oauth",
                "client_id": client_id, "subject": identity["sub"], "label": old["label"] if old else "ChatGPT",
                "base_url": RESOURCE, "model": "", "connected": True}
        result["expires_at"] = time.time() + int(result.get("expires_in", 3600))
        store.put(item, result)
        return item


browser_auth = BrowserAuth()
