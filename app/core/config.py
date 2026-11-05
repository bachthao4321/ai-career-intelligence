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
