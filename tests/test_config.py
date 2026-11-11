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


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example/v1",
        "https://user:password@api.example/v1",
        "https://api.example/v1?api_key=secret",
        "https://api.example/#fragment",
    ],
)
def test_model_endpoint_rejects_credentials_query_and_insecure_remote(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=VALID_DATABASE, llm_base_url=url)


def test_model_requests_are_disabled_by_default_and_empty_key_is_absent() -> None:
    settings = Settings(_env_file=None, database_url=VALID_DATABASE, llm_api_key="")
    assert not settings.rerank_enabled and settings.llm_daily_call_limit == 0
    assert settings.llm_api_key is None


@pytest.mark.parametrize("field", ["agent_model_base_url", "llm_base_url"])
@pytest.mark.parametrize("environment", ["test", "production"])
def test_local_lan_model_requires_exact_opt_in_host(field: str, environment: str) -> None:
    url = "http://10.20.30.40:8010/v1"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=VALID_DATABASE, **{field: url})
    settings = Settings(
        _env_file=None,
        app_env="local",
        database_url=VALID_DATABASE,
        model_lan_http_hosts=["10.20.30.40"],
        **{field: url},
    )
    assert getattr(settings, field) == url
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            app_env=environment,
            database_url=VALID_DATABASE,
            model_lan_http_hosts=["10.20.30.40"],
            **{field: url},
        )
    assert (field,) in [error["loc"] for error in exc.value.errors()]
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url=VALID_DATABASE,
            model_lan_http_hosts=["10.20.30.41"],
            **{field: url},
        )


@pytest.mark.parametrize("host", ["8.8.8.8", "169.254.169.254", "remote.example", "10.0.0.1:8010"])
def test_lan_http_allowlist_rejects_public_metadata_and_non_ip_hosts(host: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=VALID_DATABASE, model_lan_http_hosts=[host])


def test_empty_agent_key_does_not_add_bearer_authentication() -> None:
    settings = Settings(_env_file=None, database_url=VALID_DATABASE, agent_model_api_key="")
    assert settings.agent_model_api_key is None
