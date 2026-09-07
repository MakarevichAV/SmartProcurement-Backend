"""Application configuration loaded from environment variables (T005).

All settings come from the environment; a git-ignored ``backend/.env`` is used for local
development. Secrets are never hard-coded here (Constitution §15, FR-068).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Core ---
    environment: str = Field(default="local", description="local | ci | staging | production")
    database_url: str = Field(
        default="postgresql+asyncpg://sp:sp@localhost:5432/smart_procurement",
        description="Async SQLAlchemy DSN (asyncpg driver).",
    )

    # --- Auth / crypto ---
    jwt_secret: str = Field(default="dev-only-change-me", min_length=8)
    jwt_access_ttl_seconds: int = Field(default=900, ge=60)
    jwt_refresh_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=300)
    fernet_key: str = Field(
        default="",
        description="urlsafe base64 32-byte key for symmetric secret encryption; required "
        "outside local dev.",
    )

    # --- AI provider ---
    llm_provider: str = Field(default="mock", description="mock | anthropic")
    anthropic_api_key: str = Field(default="")
    anthropic_model: str = Field(default="claude-sonnet-5")

    # --- HTTP ---
    api_v1_prefix: str = Field(default="/api/v1")
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        description="Comma-separated list of allowed browser origins (credentials enabled).",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
