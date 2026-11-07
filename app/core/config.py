from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AI Career Intelligence"
    app_env: Literal["local", "test", "production"] = "local"
    database_url: SecretStr
    qdrant_url: str = "http://localhost:6333"
    dependency_timeout_seconds: float = Field(default=2, ge=0.1, le=30)
    db_statement_timeout_ms: int = Field(default=5000, ge=100, le=60000)
    deepseek_api_key: SecretStr | None = None
    deepseek_model: str | None = Field(default=None, min_length=1, max_length=100)
    deepseek_daily_call_limit: int = Field(default=0, ge=0, le=100)
    embedding_model_dir: Path = Path("models/multilingual-minilm")
    embedding_threads: int = Field(default=2, ge=1, le=8)
    qdrant_collection_prefix: str = Field(default="career_jobs", pattern=r"^[a-z][a-z0-9_]{0,48}$")
    vector_timeout_seconds: float = Field(default=30, ge=1, le=60)
    search_corpus_limit: int = Field(default=5000, ge=10, le=10_000)
    rrf_k: int = Field(default=60, ge=1, le=200)
    rrf_bm25_weight: float = Field(default=0.5, gt=0, le=4)
    rrf_dense_weight: float = Field(default=1, gt=0, le=4)
    analytics_database_url: SecretStr | None = None
    analytics_schema: str = Field(default="public", pattern=r"^[a-z][a-z0-9_]{0,62}$")
    analytics_timeout_ms: int = Field(default=1500, ge=100, le=5000)
    agent_model_enabled: bool = False
    agent_model_base_url: str = "http://localhost:11434/v1"
    agent_model_name: str = Field(default="qwen3:8b", min_length=1, max_length=100)
    agent_model_api_key: SecretStr | None = None
    agent_daily_call_limit: int = Field(default=0, ge=0, le=100)

    @field_validator("agent_model_base_url")
    @classmethod
    def validate_model_origin(cls, value: str) -> str:
        from urllib.parse import urlsplit

        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Model URL must be HTTP(S), without credentials/query/fragment")
        if url.scheme != "https" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote model endpoints require HTTPS")
        return value.rstrip("/")

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            valid = url.drivername == "postgresql+psycopg" and bool(url.host and url.database)
        except Exception:
            valid = False
        if not valid:
            raise ValueError("DATABASE_URL must be a postgresql+psycopg URL with host and database")
        return value

    @field_validator("qdrant_url")
    @classmethod
    def validate_qdrant_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("QDRANT_URL must be an HTTP(S) origin without credentials or path")
        return value.rstrip("/")
