"""Application configuration loaded from environment variables (12-factor)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings with privacy-first local defaults."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "staging", "production", "test"] = "development"
    app_name: str = "QualiText"
    app_debug: bool = True
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # Security & Auth
    secret_key: str = "dev-only-insecure-secret-key-change-me-in-production"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    jwt_algorithm: str = "HS256"

    # Database (SQLite async for fast-dev, PostgreSQL+asyncpg for Docker/prod)
    database_url: str = "sqlite+aiosqlite:///./qualitext_dev.db"

    # Object Storage (local filesystem fallback for fast-dev, S3/MinIO for Docker/prod)
    storage_backend: Literal["local", "s3"] = "local"
    local_storage_path: str = "./.local_storage"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket_name: str = "qualitext-datasets"
    s3_region: str = "us-east-1"

    # Worker & Queue
    celery_task_always_eager: bool = True
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Open-source embedding model (evaluated in Fase 0/1)
    embedding_model_name: str = "intfloat/multilingual-e5-small"
    embedding_device: str = "cpu"

    # LLM-as-a-judge placeholder (external LLM disabled by default per CLAUDE.md 0.1 #3)
    allow_external_llm: bool = False
    llm_provider: str = "placeholder"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = ""
    llm_model: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
