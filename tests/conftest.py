import os
import sys
import tempfile

import pytest

# Testler gerçek kullanıcı verisine (%APPDATA%/NIQ) dokunmasın
os.environ["NIQ_HOME"] = tempfile.mkdtemp(prefix="niq-test-")
os.environ["OPENROUTER_API_KEY"] = ""  # .env geliştirme anahtarını ezer
os.environ["OPENAI_API_KEY"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(monkeypatch):
    from app import app
    from backend.config.config import Settings

    monkeypatch.setattr(Settings, "get_api_key", staticmethod(lambda provider: None if provider != "local" else "local"))
    from backend.routes.routes import state
    from backend.services.ai_service import AIService
    monkeypatch.setattr(state, "ai_service", AIService())
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture(autouse=True)
def fresh_settings():
    """Her test varsayılan ayarlarla başlar (ayarlar dosyası testler arasında sızmasın)."""
    from backend.config.config import Settings
    path = Settings._path()
    if path.exists():
        path.unlink()
    yield
