"""Sesli sorgu: ses -> metin (sağlayıcıya göre), API sınırları."""
import base64
import io
from types import SimpleNamespace

import pytest

from backend.config.config import Settings
from backend.routes import routes
from backend.services.ai_service import AIService, AIServiceError, AIUnavailableError

WAV = b"RIFF" + b"\x24\x00\x00\x00" + b"WAVEfmt " + b"\x00" * 40


def message(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")])


class FakeChat:
    def __init__(self, text="  geçen ay satış kaç  "):
        self.params = None
        self.text = text
        self.completions = self
        self.chat = self

    def create(self, **params):
        self.params = params
        return message(self.text)


def service(provider, client):
    ai = AIService()
    ai.client = client
    ai.config = {**ai.config, "provider": provider, "model": "main-model", "max_output_tokens": 2048}
    return ai


def test_openrouter_sends_input_audio_to_stt_model():
    chat = FakeChat()
    ai = service("openrouter", chat)
    assert ai.transcribe(WAV) == "geçen ay satış kaç"
    params = chat.params
    assert params["model"] == "google/gemini-3.5-flash-lite"  # sohbet modeli değil, ses modeli
    part = params["messages"][0]["content"][1]
    assert part["type"] == "input_audio" and part["input_audio"]["format"] == "wav"
    assert base64.b64decode(part["input_audio"]["data"]) == WAV


def test_stt_model_is_configurable():
    Settings.save({"stt_model": "google/gemini-3.1-flash-lite"})
    chat = FakeChat()
    service("openrouter", chat).transcribe(WAV)
    assert chat.params["model"] == "google/gemini-3.1-flash-lite"


def test_openai_uses_transcription_endpoint():
    calls = {}

    def create(**kw):
        calls.update(kw)
        return SimpleNamespace(text=" merhaba ")

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    assert service("openai", client).transcribe(WAV) == "merhaba"
    assert calls["model"] == "gpt-4o-mini-transcribe" and calls["language"] == "tr" and calls["file"][1] == WAV


def test_local_provider_and_missing_key_are_clear_errors():
    with pytest.raises(AIServiceError, match="OpenRouter veya OpenAI"):
        service("local", FakeChat()).transcribe(WAV)
    ai = AIService()
    ai.client = None
    with pytest.raises(AIUnavailableError):
        ai.transcribe(WAV)


@pytest.fixture()
def api(client, monkeypatch):
    monkeypatch.setattr(routes.state, "ai_service", service("openrouter", FakeChat("kaç sipariş var")))
    return client


def upload(api, data=WAV, **extra):
    return api.post("/api/transcribe", data={"audio": (io.BytesIO(data), "ses.wav"), **extra},
                    content_type="multipart/form-data")


def test_transcribe_endpoint(api):
    response = upload(api)
    assert response.status_code == 200 and response.get_json()["data"]["text"] == "kaç sipariş var"


def test_transcribe_validation(api):
    assert api.post("/api/transcribe", data={}, content_type="multipart/form-data").status_code == 400
    assert upload(api, data=b"").status_code == 400
    too_big = upload(api, data=b"RIFF" + b"\x00" * (8 * 1024 * 1024 + 10))
    assert too_big.status_code == 400 and "uzun" in too_big.get_json()["error"]


def test_transcribe_without_key_returns_503(client, monkeypatch):
    ai = AIService()
    ai.client = None
    monkeypatch.setattr(routes.state, "ai_service", ai)
    response = upload(client)
    assert response.status_code == 503 and response.get_json()["error_code"] == "AI_UNAVAILABLE"


def test_stt_model_setting_roundtrip(client):
    response = client.post("/api/settings", json={"stt_model": "google/gemini-3.1-flash-lite"})
    assert response.get_json()["data"]["stt_model"] == "google/gemini-3.1-flash-lite"
