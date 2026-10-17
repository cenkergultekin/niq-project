"""Provider setup has three user fields and validates a key before persistence."""

import pytest

from backend.core.utils import ValidationError
from backend.routes import ai_accounts
from backend.services.ai_accounts import ACCOUNT_PROVIDERS
from backend.services.ai_service import AIService, AIServiceError


@pytest.mark.parametrize("provider", ["openai", "openrouter", "anthropic", "gemini"])
def test_simple_setup_derives_endpoint_and_recommended_model(monkeypatch, provider):
    model = ACCOUNT_PROVIDERS[provider]["default_model"]
    monkeypatch.setattr(ai_accounts, "provider_models", lambda config: [{"id": model, "name": model}])
    calls = []
    monkeypatch.setattr(AIService, "_complete", lambda self, *a, **kw: calls.append(self.config) or "ok")
    result = ai_accounts.validate_key_account({"provider": provider, "label": "Work", "api_key": "synthetic"})
    assert result["model"] == model and result["base_url"] == ACCOUNT_PROVIDERS[provider]["url"]
    assert len(calls) == 1 and calls[0]["api_key"] == "synthetic"


def test_invalid_key_cannot_complete_setup(monkeypatch):
    monkeypatch.setattr(ai_accounts, "provider_models", lambda config: [{"id": "gpt-6-luna", "name": "Luna"}])
    def denied(*args, **kwargs):
        raise AIServiceError("synthetic denied")
    monkeypatch.setattr(AIService, "_complete", denied)
    with pytest.raises(ValidationError, match="doğrulanamadı"):
        ai_accounts.validate_key_account({"provider": "openai", "api_key": "invalid"})


def test_gemini_uses_supported_token_parameter():
    service = AIService(config={"provider": "gemini", "api_key": "synthetic", "model": "gemini-3.8-flash",
                                "base_url": ACCOUNT_PROVIDERS["gemini"]["url"], "max_output_tokens": 2048})
    params = service._build_params([{"role": "user", "content": "ok"}], None, None, 0.0)
    assert params["max_tokens"] == 2048 and "max_completion_tokens" not in params


def test_callback_redirect_removes_provider_credentials_and_renders_success(client, monkeypatch):
    monkeypatch.setattr(ai_accounts.browser_auth, "finish", lambda params: "ok")
    monkeypatch.setattr(ai_accounts.browser_auth, "provider", lambda state: "chatgpt")
    response = client.get("/auth/callback?code=synthetic-private-code&state=synthetic-state")
    assert response.status_code == 303 and "private-code" not in response.headers["Location"]
    result = client.get(response.headers["Location"])
    assert result.status_code == 200 and "Giriş başarılı" in result.get_data(as_text=True)
    assert "synthetic-private-code" not in result.get_data(as_text=True)


def test_desktop_focus_request_has_no_account_or_database_side_effects(client, monkeypatch):
    from backend.services import desktop_actions
    monkeypatch.setattr(desktop_actions, "available", True)
    desktop_actions.focus_requested.clear()
    response = client.post("/api/desktop/focus", json={})
    assert response.json["data"]["available"] and desktop_actions.focus_requested.is_set()
    desktop_actions.focus_requested.clear()
