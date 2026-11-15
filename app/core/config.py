from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

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
    rrf_bm25_weight: float = Field(default=2, gt=0, le=8)
    rrf_dense_weight: float = Field(default=1, gt=0, le=4)
    analytics_database_url: SecretStr | None = None
    analytics_schema: str = Field(default="public", pattern=r"^[a-z][a-z0-9_]{0,62}$")
    analytics_timeout_ms: int = Field(default=1500, ge=100, le=5000)
    model_lan_http_hosts: list[str] = Field(default_factory=list, max_length=8)
    agent_model_enabled: bool = False
    agent_model_base_url: str = "http://localhost:11434/v1"
    agent_model_name: str = Field(default="qwen3:8b", min_length=1, max_length=100)
    agent_model_api_key: SecretStr | None = None
    agent_daily_call_limit: int = Field(default=0, ge=0, le=100)
    cv_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    cv_capacity: int = Field(default=1000, ge=1, le=10000)
    cv_cleanup_interval_seconds: int = Field(default=60, ge=5, le=300)
    llm_global_daily_call_limit: int = Field(default=100, ge=0, le=10000)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = Field(default="qwen3:8b", min_length=1, max_length=100)
    llm_model_revision: str = Field(default="unverified", min_length=1, max_length=100)
    llm_api_key: SecretStr | None = None
    llm_daily_call_limit: int = Field(default=0, ge=0, le=1000)
    llm_timeout_seconds: float = Field(default=15, ge=1, le=30)
    llm_max_attempts: int = Field(default=2, ge=1, le=3)
    llm_min_interval_seconds: float = Field(default=0.5, ge=0, le=10)
    llm_concurrency: int = Field(default=2, ge=1, le=4)
    llm_cache_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    llm_cache_capacity: int = Field(default=1000, ge=1, le=10000)
    rerank_enabled: bool = False
    rerank_candidates: int = Field(default=10, ge=2, le=10)
    rerank_timeout_seconds: float = Field(default=5, ge=0.1, le=15)
    demo_auth_enabled: bool = False
    demo_username: str = Field(default="viewer", min_length=1, max_length=80)
    demo_password: SecretStr | None = None
    demo_cookie_secret: SecretStr | None = None
    admin_key: SecretStr | None = None
    public_origin: str | None = None
    demo_session_seconds: int = Field(default=7200, ge=60, le=86400)
    client_limits_enabled: bool = False
    client_requests_per_minute: int = Field(default=60, ge=1, le=600)
    client_llm_routes_per_day: int = Field(default=10, ge=0, le=100)
    trusted_proxy_ip: str | None = None
    user_session_days: int = Field(default=30, ge=1, le=90)
    auth_attempts_per_minute: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def production_controls(self) -> "Settings":
        from ipaddress import ip_address
        from urllib.parse import urlsplit

        if self.trusted_proxy_ip:
            ip_address(self.trusted_proxy_ip)
        if self.public_origin:
            parsed = urlsplit(self.public_origin)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("PUBLIC_ORIGIN must be an HTTPS origin")
            self.public_origin = self.public_origin.rstrip("/")
        if self.app_env == "production" and (
            not self.demo_auth_enabled or not self.client_limits_enabled or not self.public_origin
        ):
            raise ValueError(
                "Production requires demo authentication, client limits and PUBLIC_ORIGIN"
            )
        if self.demo_auth_enabled or self.client_limits_enabled:
            if not self.demo_cookie_secret or len(self.demo_cookie_secret.get_secret_value()) < 32:
                raise ValueError(
                    "Demo/client controls require a cookie secret of at least 32 characters"
                )
        if self.demo_auth_enabled:
            if not self.demo_password or len(self.demo_password.get_secret_value()) < 16:
                raise ValueError("Demo password must contain at least 16 characters")
        if self.app_env == "production" and (
            not self.admin_key or len(self.admin_key.get_secret_value()) < 32
        ):
            raise ValueError("Production requires an admin key of at least 32 characters")
        if self.admin_key and len(self.admin_key.get_secret_value()) < 32:
            raise ValueError("Admin key must contain at least 32 characters")
        secrets = [
            value.get_secret_value()
            for value in (self.demo_password, self.demo_cookie_secret, self.admin_key)
            if value
        ]
        if len(set(secrets)) != len(secrets):
            raise ValueError("Demo password, cookie secret and admin key must be different")
        if self.app_env == "production":
            if any(value.startswith("replace_") for value in secrets):
                raise ValueError("Replace production secret placeholders")
            for value in (self.database_url, self.analytics_database_url):
                if value and (make_url(value.get_secret_value()).password or "").startswith(
                    "replace_"
                ):
                    raise ValueError("Replace production database password placeholders")
        return self

    @field_validator("llm_api_key", "agent_model_api_key", mode="before")
    @classmethod
    def empty_model_key(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("model_lan_http_hosts")
    @classmethod
    def validate_lan_hosts(cls, values: list[str]) -> list[str]:
        networks = [
            IPv4Network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        ]
        for host in values:
            address = IPv4Address(host)
            if not any(address in network for network in networks):
                raise ValueError("LAN HTTP exceptions must be explicit RFC1918 IPv4 addresses")
        return values

    @field_validator("agent_model_base_url", "llm_base_url")
    @classmethod
    def validate_model_origin(cls, value: str, info: ValidationInfo) -> str:
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
            allowed_lan = info.data.get("app_env") == "local" and url.hostname in info.data.get(
                "model_lan_http_hosts", []
            )
            if not allowed_lan:
                raise ValueError(
                    "Remote model endpoints require HTTPS or an explicit local LAN host"
                )
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
