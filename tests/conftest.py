import pytest

from app.core.config import Settings
from app.schemas.ingestion import JobRecord


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+psycopg://test:secret@127.0.0.1:1/test",
        qdrant_url="http://127.0.0.1:1",
        dependency_timeout_seconds=0.1,
    )


@pytest.fixture
def record() -> JobRecord:
    return JobRecord(
        source="synthetic",
        source_job_id="job-1",
        source_url="https://example.com/jobs/1",
        source_terms_url="https://example.com/terms",
        title="Backend Developer",
        description="Cần Python, Postgres và Kubernetes. Không yêu cầu JavaScript.",
        location="Hồ Chí Minh, Việt Nam",
        company="Synthetic Fixture",
        raw_payload={"fixture": True},
    )
