import pytest
from pydantic import ValidationError

from app.core.config import Settings

VALID_DATABASE = "postgresql+psycopg://career:p%40ss@localhost:5432/career"


def test_configuration_reads_environment_and_masks_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", VALID_DATABASE)
    monkeypatch.setenv("APP_ENV", "test")
    settings = Settings(_env_file=None)
    assert settings.database_url.get_secret_value() == VALID_DATABASE
    assert settings.app_env == "test"
    assert VALID_DATABASE not in repr(settings)
    assert "p%40ss" not in settings.model_dump_json()


@pytest.mark.parametrize("url", ["sqlite:///test.db", "postgresql://localhost/test", "not-a-url"])
def test_configuration_rejects_wrong_database_driver(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=url)


@pytest.mark.parametrize(
    "url", ["ftp://localhost", "http://user:secret@localhost", "http://localhost/path"]
)
def test_configuration_rejects_invalid_qdrant_origin(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=VALID_DATABASE, qdrant_url=url)


@pytest.mark.parametrize("timeout", [0, -1, 31])
def test_configuration_rejects_unbounded_timeout(timeout: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=VALID_DATABASE, dependency_timeout_seconds=timeout)
