"""Synthetic account vault and real signed JWTs; no live credentials or consent."""
import base64
import hashlib
import json
import time
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.config.config import Settings
from backend.core.utils import ValidationError
from backend.services import ai_auth
from backend.services.ai_accounts import AccountStore, endpoint, selected_config
from backend.services.ai_auth import BrowserAuth


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("NIQ_HOME", str(tmp_path))
    values = {}
    monkeypatch.setattr("keyring.set_password", lambda service, name, value: values.__setitem__(name, value))
    monkeypatch.setattr("keyring.get_password", lambda service, name: values.get(name))
    monkeypatch.setattr("keyring.delete_password", lambda service, name: values.pop(name, None))
    from backend.routes import ai_accounts
    monkeypatch.setattr(ai_accounts, "validate_key_account", lambda data: {**data, "model": data.get("model") or "synthetic-model"})
    return AccountStore(), values


@pytest.fixture
def auth(vault, monkeypatch):
    service = BrowserAuth()
    urls = []
    monkeypatch.setattr(ai_auth.webbrowser, "open", lambda url: urls.append(url) or True)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(ai_auth.JWKS, "get_signing_key_from_jwt", lambda token: NS(key=private.public_key()))
    return service, urls, private


def begin(auth, account_id=""):
    service, urls, _ = auth
    result = service.start("chatgpt", "http://127.0.0.1:3456/auth/callback", account_id)
    return result["attempt"], {k: v[0] for k, v in parse_qs(urlsplit(urls[-1]).query).items()}


def token(auth, attempt, **overrides):
    service, _, private = auth
    claims = {"iss": ai_auth.ISSUER, "aud": "oaiapp_test", "sub": "synthetic-subject", "exp": int(time.time()) + 600,
              "nonce": service._pending[attempt]["nonce"], **overrides}
    return {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "scope": ai_auth.SCOPES,
            "expires_in": 3600, "id_token": jwt.encode(claims, private, algorithm="RS256")}


def test_multiple_keys_remain_separate_and_never_on_disk(vault):
    store, _ = vault
    one = store.add_key({"provider": "openai", "api_key": "private-one", "model": "one", "label": "Finance"})
    two = store.add_key({"provider": "openai", "api_key": "private-two", "model": "two", "label": "Research"})
    assert one["id"] != two["id"]
    assert store.configure(one["id"])["api_key"] == "private-one"
    assert store.configure(two["id"])["api_key"] == "private-two"
    text = (Settings._path().parent / "ai-accounts.json").read_text()
    assert "private-one" not in text and "private-two" not in text


def test_large_oauth_credentials_fit_windows_vault_and_rotate_atomically(vault, monkeypatch):
    store, values = vault
    item = store.add_key({"provider": "openai", "api_key": "initial", "model": "model"})
    def write(service, name, value):
        assert len(value.encode("utf-16-le")) <= 2560
        values[name] = value
    monkeypatch.setattr("keyring.set_password", write)
    first = {"access_token": "a" * 13000, "refresh_token": "r" * 6000, "id_token": "j" * 15000}
    store.set_secret(item["id"], first)
    assert store.secret(item["id"]) == first
    previous_keys = set(values)
    store.set_secret(item["id"], {"access_token": "new" * 3000})
    assert store.secret(item["id"])["access_token"] == "new" * 3000
    assert not (previous_keys - {"ai-account:" + item["id"]}) & set(values)
    snapshot = dict(values)
    def fail_pointer(service, name, value):
        if name == "ai-account:" + item["id"]:
            raise RuntimeError("synthetic vault failure")
        write(service, name, value)
    monkeypatch.setattr("keyring.set_password", fail_pointer)
    with pytest.raises(ValidationError):
        store.set_secret(item["id"], first)
    assert values == snapshot
    store.disconnect(item["id"])
    assert values == {}


@pytest.mark.parametrize("url", ["http://company.example/v1", "https://user:secret@company.example/v1",
                                 "https://company.example/v1?key=secret", "file:///secret"])
def test_unsafe_endpoints_rejected(url):
    with pytest.raises(ValidationError):
        endpoint("compatible", url)


def test_azure_autofills_v1_and_requires_real_resource():
    assert endpoint("azure", "https://finance.openai.azure.com/") == "https://finance.openai.azure.com/openai/v1"
    assert endpoint("azure", "https://finance.services.ai.azure.com/openai/v1/").endswith("/openai/v1")
    for url in ("http://finance.openai.azure.com", "https://evilazure.com", "https://finance.openai.azure.com/other"):
        with pytest.raises(ValidationError):
            endpoint("azure", url)


def test_initial_registration_pkce_scopes_host_identity(auth, vault):
    service, _, _ = auth
    attempt, query = begin(auth)
    assert query["client_id"] == "dynamic_agent_client" and query["agent_name_hint"] == "NIQ"
    assert query["scope"] == ai_auth.SCOPES and query["resource"] == ai_auth.RESOURCE
    expected = base64.urlsafe_b64encode(hashlib.sha256(service._pending[attempt]["verifier"].encode()).digest()).decode().rstrip("=")
    assert query["code_challenge"] == expected and query["code_challenge_method"] == "S256"
    assert query["ext_agent_host_id"] == vault[0].host_id() == AccountStore().host_id()
    another, other = begin(auth)
    assert attempt != another and query["nonce"] != other["nonce"]


def test_signed_identity_exchange_and_single_use(auth, vault, monkeypatch):
    service, _, _ = auth
    attempt, query = begin(auth)
    requests = []
    def exchange(url, **kwargs):
        requests.append(kwargs["data"])
        return token(auth, attempt)
    monkeypatch.setattr(ai_auth, "_post", exchange)
    service.finish({"state": attempt, "code": "one-code", "client_id": "oaiapp_test"})
    item = vault[0].list()[0]
    assert item["client_id"] == "oaiapp_test" and item["subject"] == "synthetic-subject"
    assert requests[0]["client_id"] == "oaiapp_test" and requests[0]["redirect_uri"] == query["redirect_uri"]
    assert "verifier" not in service._pending[attempt] and "nonce" not in service._pending[attempt]
    assert Settings.load()["ai_account_id"] == ""  # no implicit billing switch
    with pytest.raises(ValidationError):
        service.finish({"state": attempt, "code": "one-code", "client_id": "oaiapp_test"})
    assert len(requests) == 1


@pytest.mark.parametrize("change", [{"aud": "wrong"}, {"iss": "https://evil.example"}, {"nonce": "wrong"}, {"exp": 1}])
def test_invalid_signed_claims_never_persist(auth, vault, monkeypatch, change):
    attempt, _ = begin(auth)
    monkeypatch.setattr(ai_auth, "_post", lambda *a, **kw: token(auth, attempt, **change))
    with pytest.raises(ValidationError):
        auth[0].finish({"state": attempt, "code": "code", "client_id": "oaiapp_test"})
    assert vault[0].list() == [] and vault[1] == {}


def test_forged_signature_rejected(auth, vault, monkeypatch):
    attempt, _ = begin(auth)
    forged = token(auth, attempt)
    claims = jwt.decode(forged["id_token"], options={"verify_signature": False})
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged["id_token"] = jwt.encode(claims, other_key, algorithm="RS256")
    monkeypatch.setattr(ai_auth, "_post", lambda *a, **kw: forged)
    with pytest.raises(ValidationError):
        auth[0].finish({"state": attempt, "code": "code", "client_id": "oaiapp_test"})
    assert not vault[0].list()


@pytest.mark.parametrize("client_id", [None, "dynamic_agent_client"])
def test_registration_requires_issued_client(auth, vault, monkeypatch, client_id):
    attempt, _ = begin(auth)
    monkeypatch.setattr(ai_auth, "_post", lambda *a, **kw: pytest.fail("must not exchange"))
    with pytest.raises(ValidationError):
        auth[0].finish({"state": attempt, "code": "code", "client_id": client_id})
    assert not vault[0].list()


def test_wrong_state_expired_cancelled_and_denial_never_exchange(auth, monkeypatch):
    monkeypatch.setattr(ai_auth, "_post", lambda *a, **kw: pytest.fail("must not exchange"))
    service = auth[0]
    with pytest.raises(ValidationError):
        service.finish({"state": "wrong", "code": "code"})
    attempt, _ = begin(auth)
    service._pending[attempt]["expires"] = 1
    with pytest.raises(ValidationError):
        service.finish({"state": attempt, "code": "code"})
    attempt, _ = begin(auth)
    service.cancel(attempt)
    with pytest.raises(ValidationError):
        service.finish({"state": attempt, "code": "code"})
    attempt, _ = begin(auth)
    with pytest.raises(ValidationError):
        service.finish({"state": attempt, "error": "access_denied"})


def test_identity_without_plan_permission_is_not_a_chat_account(auth, vault, monkeypatch):
    attempt, _ = begin(auth)
    result = token(auth, attempt)
    result["scope"] = "openid email profile"
    monkeypatch.setattr(ai_auth, "_post", lambda *a, **kw: result)
    with pytest.raises(ValidationError, match="plan"):
        auth[0].finish({"state": attempt, "code": "code", "client_id": "oaiapp_test"})
    assert not vault[0].list()


def test_returning_account_preserves_registration_and_rejects_other_identity(auth, vault, monkeypatch):
    attempt, _ = begin(auth)
    monkeypatch.setattr(ai_auth, "_post", lambda *a, **kw: token(auth, attempt))
    auth[0].finish({"state": attempt, "code": "code", "client_id": "oaiapp_test"})
    original = vault[0].list()[0]
    attempt, query = begin(auth, original["id"])
    assert query["client_id"] == "oaiapp_test" and "agent_name_hint" not in query
    monkeypatch.setattr(ai_auth, "_post", lambda *a, **kw: token(auth, attempt, sub="other-person"))
    with pytest.raises(ValidationError, match="hesabı"):
        auth[0].finish({"state": attempt, "code": "code"})
    assert vault[0].secret(original["id"])["access_token"] == "synthetic-access"
    attempt, _ = begin(auth, original["id"])
    with pytest.raises(ValidationError, match="kayıt"):
        auth[0].finish({"state": attempt, "code": "code", "client_id": "different-client"})


def test_refresh_rotates_omits_scope_and_keeps_registration(auth, vault, monkeypatch):
    attempt, _ = begin(auth)
    result = token(auth, attempt)
    monkeypatch.setattr(ai_auth, "_post", lambda *a, **kw: result)
    auth[0].finish({"state": attempt, "code": "code", "client_id": "oaiapp_test"})
    item = vault[0].list()[0]
    secret = vault[0].secret(item["id"])
    secret["expires_at"] = 1
    vault[0].set_secret(item["id"], secret)
    sent = []
    def refresh(url, **kw):
        sent.append(kw["data"])
        return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
    monkeypatch.setattr(ai_auth, "_post", refresh)
    assert vault[0].chatgpt_credentials(item["id"])["access_token"] == "new-access"
    assert vault[0].chatgpt_credentials(item["id"])["refresh_token"] == "new-refresh"
    assert len(sent) == 1 and "scope" not in sent[0] and sent[0]["client_id"] == "oaiapp_test"


def test_separate_voice_requires_explicit_billing_selection(vault):
    item = vault[0].add_key({"provider": "anthropic", "api_key": "synthetic", "model": "model"})
    Settings.save({"ai_account_id": item["id"]})
    with pytest.raises(ValidationError, match="ayrı"):
        selected_config("voice")
    Settings.save({"voice_account_id": item["id"]})
    with pytest.raises(ValidationError, match="Ses"):
        selected_config("voice")


def test_api_approval_public_metadata_and_disconnect_no_fallback(client, vault):
    body = {"provider": "openai", "api_key": "synthetic-secret", "model": "model", "label": "Finance"}
    assert client.post("/api/ai/accounts", json=body).status_code == 409
    assert not vault[0].list()
    response = client.post("/api/ai/accounts", json={**body, "approved": True})
    assert response.status_code == 200
    item = response.json["data"]
    assert "synthetic-secret" not in json.dumps(client.get("/api/ai/accounts").json)
    assert client.post("/api/ai/accounts/" + item["id"] + "/disconnect", json={}).status_code == 409
    assert client.post("/api/ai/accounts/" + item["id"] + "/disconnect", json={"approved": True}).status_code == 200
    assert Settings.load()["ai_account_id"] == item["id"]
    with pytest.raises(ValidationError):
        selected_config()


def test_chatgpt_catalog_visibility_order_and_selection(client, vault, monkeypatch):
    from backend.routes import ai_accounts
    item = {"id": "test-account", "provider": "chatgpt", "auth": "oauth", "label": "ChatGPT", "client_id": "oaiapp_test",
            "subject": "synthetic", "model": "", "base_url": ai_auth.RESOURCE, "connected": True}
    vault[0].put(item, {"access_token": "synthetic", "scope": ai_auth.SCOPES, "expires_at": time.time() + 3600})
    result = {"models": [{"slug": "second", "display_name": "Second", "visibility": "list"},
                         {"slug": "hidden", "display_name": "Hidden", "visibility": "hidden"},
                         {"slug": "first", "display_name": "First", "visibility": "list"}]}
    monkeypatch.setattr(ai_accounts.requests, "get", lambda *a, **kw: NS(raise_for_status=lambda: None, json=lambda: result))
    assert [m["id"] for m in client.get("/api/ai/accounts/test-account/models").json["data"]] == ["second", "first"]
    assert client.post("/api/ai/select", json={"account_id": item["id"], "model": "hidden"}).status_code == 400
    assert client.post("/api/ai/select", json={"account_id": item["id"], "model": "first"}).status_code == 200
    public = json.dumps(client.get("/api/ai/accounts").json)
    assert "access_token" not in public and "subject" not in public and "client_id" not in public


def test_openrouter_pkce_creates_distinct_key(auth, vault, monkeypatch):
    service, urls, _ = auth
    state = service.start("openrouter", "http://127.0.0.1:3456/auth/callback")["attempt"]
    query = parse_qs(urlsplit(urls[-1]).query)
    assert query["state"] == [state] and query["code_challenge_method"] == ["S256"]
    sent = []
    monkeypatch.setattr(ai_auth, "_post", lambda url, **kw: sent.append(kw) or {"key": "synthetic-openrouter"})
    service.finish({"state": state, "code": "single-use"})
    item = vault[0].list()[0]
    assert item["provider"] == "openrouter" and sent[0]["json"]["code_verifier"]
    assert vault[0].secret(item["id"])["api_key"] == "synthetic-openrouter"


def test_account_selection_keeps_inflight_service_bound(client, vault):
    from backend.routes.routes import state
    one = vault[0].add_key({"provider": "openai", "api_key": "one", "model": "model-one"})
    two = vault[0].add_key({"provider": "openai", "api_key": "two", "model": "model-two"})
    client.post("/api/ai/select", json={"account_id": one["id"]})
    in_flight = state.ai_service
    client.post("/api/ai/select", json={"account_id": two["id"]})
    assert in_flight.config["account_id"] == one["id"] and in_flight.config["api_key"] == "one"
    assert state.ai_service.config["account_id"] == two["id"]


def test_callback_log_scrubs_codes_and_state():
    import logging

    from backend.core.utils import OAuthLogFilter
    record = logging.LogRecord("werkzeug", logging.INFO, "", 1, 'GET /auth/callback?code=%s&state=%s', ("secret-code", "secret-state"), None)
    assert OAuthLogFilter().filter(record)
    assert "secret" not in record.getMessage()


def test_no_auth_or_account_mutation_from_cross_origin(client, auth):
    response = client.post("/api/ai/auth/start", json={"provider": "chatgpt", "approved": True},
                           headers={"Origin": "http://localhost:9876"})
    assert response.status_code == 403 and not auth[1]


def test_oauth_local_callback_response_contains_no_callback_credentials(client, auth):
    response = client.get("/auth/callback?state=unknown&code=secret-code&client_id=secret-client", follow_redirects=True)
    assert response.status_code == 400
    assert "secret" not in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store" and response.headers["Referrer-Policy"] == "no-referrer"
    assert response.request.path.startswith('/auth/result/')
    assert 'niq.png' in response.get_data(as_text=True) and 'chatgpt-logo-white.svg' in response.get_data(as_text=True)


def test_refresh_cross_process_serialization(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    from pathlib import Path
    monkeypatch.setenv("NIQ_HOME", str(tmp_path))
    item = {"id": "synthetic-account", "provider": "chatgpt", "connected": True, "client_id": "oaiapp_test"}
    AccountStore().put(item)
    (tmp_path / "synthetic-vault.json").write_text(json.dumps({"scope": ai_auth.SCOPES, "expires_at": 1, "refresh_token": "old"}))
    script = r'''
import json, time
from pathlib import Path
import keyring
from backend.services import ai_auth
from backend.services.ai_accounts import AccountStore
from backend.config.config import data_dir
root = data_dir()
path = root / 'synthetic-vault.json'
keyring.get_password = lambda *a: path.read_text()
keyring.set_password = lambda service, name, value: path.write_text(value)
def refresh(item, secret):
    with (root / 'refresh-count.txt').open('a') as file: file.write('refresh\n')
    time.sleep(0.3)
    return {**secret, 'access_token':'new', 'refresh_token':'rotated', 'expires_at':time.time()+3600}
ai_auth.refresh_chatgpt = refresh
assert AccountStore().chatgpt_credentials('synthetic-account')['refresh_token'] == 'rotated'
'''
    processes = [subprocess.Popen([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                                  env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr.decode(errors="replace")
    assert (tmp_path / "refresh-count.txt").read_text().splitlines() == ["refresh"]
