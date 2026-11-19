"""Settings loaded from backend/.env (and real env vars, which take priority)."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # Supabase
    supabase_url: str
    supabase_anon_key: SecretStr | None = None
    supabase_service_role_key: SecretStr | None = None  # backend only, never sent to the frontend
    supabase_jwt_secret: SecretStr | None = None  # legacy HS256 tokens; new projects use JWKS (ES256)
    database_url: SecretStr

    # OpenAI (used from task 1.6)
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    openai_embed_model: str = "text-embedding-3-small"

    # Channels (used from tasks 1.5 / 1.13)
    channel_encryption_key: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    public_base_url: str = "http://localhost:8000"

    # Frontend origins allowed by CORS (comma-separated)
    cors_origins: str = "http://localhost:5173"

    @property
    def supabase_issuer(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_issuer}/.well-known/jwks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
