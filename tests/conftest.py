import pytest

from app.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+psycopg://test:secret@127.0.0.1:1/test",
        qdrant_url="http://127.0.0.1:1",
        dependency_timeout_seconds=0.1,
    )
