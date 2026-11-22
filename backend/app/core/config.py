"""Application configuration loaded from environment variables (12-factor)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_INSECURE_SECRET_KEY = "dev-only-insecure-secret-key-change-me-in-production"


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
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # Security & Auth
    secret_key: str = DEFAULT_INSECURE_SECRET_KEY
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    jwt_algorithm: str = "HS256"
    login_rate_limit_max_attempts: int = 10
    login_rate_limit_window_seconds: int = 60

    # Enterprise SSO (OIDC / SAML) - opt-in
    oidc_enabled: bool = False
    oidc_provider_name: str = "Corporate SSO"
    oidc_issuer_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://localhost:5173/auth/sso/callback"
    oidc_jwks_json: str = ""
    oidc_hs256_secret: str = ""
    oidc_default_workspace_slug: str = "tim-nlp-inti"

    saml_enabled: bool = False
    saml_idp_sso_url: str = ""
    saml_entity_id: str = "qualitext-sp"
    saml_acs_url: str = "http://localhost:8000/api/v1/auth/saml/acs"
    saml_shared_secret: str = ""

    # Webhooks
    webhook_timeout_seconds: float = 5.0
    webhook_allow_private_targets: bool = False

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

    # Open-source embedding model & vector index backend
    embedding_model_name: str = "intfloat/multilingual-e5-small"
    embedding_device: str = "cpu"
    embedding_backend: Literal["auto", "local_hash", "sentence_transformers"] = "auto"
    vector_index_backend: Literal["auto", "bruteforce", "lsh", "faiss", "pgvector"] = "auto"

    # Observability
    otel_service_name: str = "qualitext-api"

    # LLM-as-a-judge placeholder (external LLM disabled by default per CLAUDE.md 0.1 #3)
    allow_external_llm: bool = False
    llm_provider: str = "placeholder"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = ""
    llm_model: str = ""

    # VLM verification placeholder (PDF page-image vs extracted text/table)
    vlm_provider: str = "placeholder"
    vlm_base_url: str = "http://localhost:11434/v1"
    vlm_api_key: str = ""
    vlm_model: str = ""

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        """Reject insecure default secret keys when running in production."""
        if self.app_env == "production" and (
            self.secret_key == DEFAULT_INSECURE_SECRET_KEY or len(self.secret_key) < 32
        ):
            raise ValueError(
                "SECRET_KEY must be configured with a strong secret (>= 32 chars) in production."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
