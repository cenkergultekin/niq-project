"""
Configuration management for SQL Agent application.
Handles environment variables and application settings.
"""
import os
import sys
from dotenv import load_dotenv
from typing import Optional

# Load environment variables
# PyInstaller için .env dosyasını bundle içinden yükle
if getattr(sys, 'frozen', False):
    # PyInstaller bundle içindeyiz
    import sys
    bundle_dir = sys._MEIPASS
    env_path = os.path.join(bundle_dir, '.env')
    if os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        # .env bulunamazsa, executable'ın yanında ara
        exe_dir = os.path.dirname(sys.executable)
        env_path = os.path.join(exe_dir, '.env')
        if os.path.exists(env_path):
            load_dotenv(env_path)
else:
    load_dotenv()


class Config:
    """Application configuration class."""
    
    # Flask Configuration
    SECRET_KEY: str = os.getenv('SECRET_KEY', os.urandom(24))
    DEBUG: bool = os.getenv('DEBUG', 'False').lower() == 'true'
    
    # OpenAI Configuration
    OPENAI_API_KEY: Optional[str] = os.getenv('OPENAI_API_KEY')
    OPENAI_MODEL: str = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')

    # OpenRouter Configuration (OpenAI-uyumlu; OPENAI_API_KEY yoksa kullanılır)
    OPENROUTER_API_KEY: Optional[str] = os.getenv('OPENROUTER_API_KEY')
    OPENROUTER_MODEL: str = os.getenv('OPENROUTER_MODEL', 'openai/gpt-6-luna')
    # OPENROUTER_MODEL için seçenekler (fiyat: $0.10 giriş / $0.50 çıkış, 1M token)
    OPENROUTER_MODEL_OPTIONS: tuple = (
        'openai/gpt-6-luna',
        'openai/gpt-6-luna-pro',
        'anthropic/claude-haiku-5.5',
        'openai/gpt-4o-mini',
    )
    OPENROUTER_BASE_URL: str = os.getenv('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')
    
    # Database Configuration
    DB_CONNECTION_TIMEOUT: int = int(os.getenv('DB_CONNECTION_TIMEOUT', '5'))
    DEFAULT_ODBC_DRIVER: str = os.getenv('DEFAULT_ODBC_DRIVER', 'ODBC Driver 17 for SQL Server')
    KEYRING_SERVICE: str = os.getenv('KEYRING_SERVICE', 'sql-agent-cursor')
    
    # Application Configuration
    MAX_TABLES_PER_QUERY: int = int(os.getenv('MAX_TABLES_PER_QUERY', '10'))
    MAX_QUERY_LENGTH: int = int(os.getenv('MAX_QUERY_LENGTH', '1000'))
    
    @classmethod
    def get_provider(cls) -> Optional[str]:
        """Aktif AI sağlayıcısı: OPENAI_API_KEY varsa openai, yoksa openrouter."""
        if cls.OPENAI_API_KEY:
            return 'openai'
        if cls.OPENROUTER_API_KEY:
            return 'openrouter'
        return None

    @classmethod
    def validate_config(cls) -> None:
        """Validate required configuration values."""
        if not cls.get_provider():
            raise ValueError("OPENAI_API_KEY or OPENROUTER_API_KEY environment variable is required")

    @classmethod
    def get_model(cls) -> str:
        """Aktif sağlayıcının model adı."""
        return cls.OPENROUTER_MODEL if cls.get_provider() == 'openrouter' else cls.OPENAI_MODEL

    @classmethod
    def get_openai_config(cls) -> dict:
        """OpenAI SDK client ayarları (OpenRouter için base_url eklenir)."""
        if cls.get_provider() == 'openrouter':
            return {'api_key': cls.OPENROUTER_API_KEY, 'base_url': cls.OPENROUTER_BASE_URL}
        return {'api_key': cls.OPENAI_API_KEY}
