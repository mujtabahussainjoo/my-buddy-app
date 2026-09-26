"""Centralized, startup-validated application configuration.

All runtime settings live here and are read once at import time. Sensitive
values are never logged; only presence/mask status is reported.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "test", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    APP_NAME: str = "MyAIBuddy"
    APP_ENV: AppEnv = "development"
    APP_URL: str = "http://localhost:8000"
    API_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"
    VERSION: str = "0.1.0"

    # Database
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "myaibuddy"
    POSTGRES_USER: str = "myaibuddy"
    POSTGRES_PASSWORD: str = ""
    DATABASE_URL: str = ""
    ECHO_SQL: bool = False

    # Authentication and security
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    CORS_ORIGINS: str = "http://localhost:5173"
    PASSWORD_MIN_LENGTH: int = 8

    # AI provider keys (always empty in logs)
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    HUGGINGFACE_API_TOKEN: str = ""
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"

    # Search and external tools
    TAVILY_API_KEY: str = ""
    SERPAPI_API_KEY: str = ""
    FIRECRAWL_API_KEY: str = ""

    # Optional image generation
    STABILITY_API_KEY: str = ""
    REPLICATE_API_TOKEN: str = ""

    # Observability
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "myaibuddy"
    SENTRY_DSN: str = ""

    # MCP
    MCP_SERVER_URLS: str = ""
    MCP_AUTH_TOKEN: str = ""

    # RAG / embeddings
    EMBEDDING_PROVIDER: str = "keyword"  # keyword | openai | huggingface | ollama
    EMBEDDING_DIM: int = 384
    DEFAULT_EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    CHUNK_SIZE: int = 700
    CHUNK_OVERLAP: int = 70
    RETRIEVAL_TOP_K: int = 6
    RETRIEVAL_MIN_SCORE: float = 0.10
    RETRIEVAL_CHUNKS_PER_DOC: int = 2
    RETRIEVAL_MAX_CONTEXT_CHARS: int = 12000
    MAX_UPLOAD_MB: int = 50
    MAX_UPLOAD_FILES: int = 20
    UPLOAD_DIR: str = "var/uploads"

    # Rate limits
    RATE_LIMIT_AUTH: str = "10/minute"
    RATE_LIMIT_CHAT: str = "30/minute"
    RATE_LIMIT_UPLOAD: str = "15/minute"

    # Timeouts for external AI calls (seconds)
    LLM_TIMEOUT_SECONDS: float = 60.0
    TOOL_TIMEOUT_SECONDS: float = 30.0

    @model_validator(mode="after")
    def _finalize(self) -> Settings:
        if self.APP_ENV != "test" and not self.JWT_SECRET_KEY:
            # Development convenience only; production must set a real secret.
            self.JWT_SECRET_KEY = secrets.token_urlsafe(48)

        if not self.DATABASE_URL:
            self.DATABASE_URL = (
                f"postgresql+asyncpg://{self.POSTGRES_USER}:{quote_plus(self.POSTGRES_PASSWORD)}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def configured_providers(self) -> list[str]:
        """Providers that have keys (or base URLs) configured. Never logs values."""
        configured: list[str] = []
        if self.OPENAI_API_KEY:
            configured.append("openai")
        if self.ANTHROPIC_API_KEY:
            configured.append("anthropic")
        if self.GOOGLE_API_KEY:
            configured.append("google")
        if self.OLLAMA_BASE_URL:
            configured.append("ollama")
        return configured


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
