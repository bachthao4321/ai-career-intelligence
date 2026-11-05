import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.mark.parametrize(
    "path", ["/jobs?limit=0", "/jobs?limit=101", "/jobs?offset=-1", "/jobs/not-a-uuid"]
)
def test_jobs_api_rejects_invalid_input_before_querying_database(
    settings: Settings, path: str
) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get(path).status_code == 422


def test_jobs_api_hides_database_connection_errors(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/jobs")
    assert response.status_code == 503
    assert response.json() == {"detail": "Job storage unavailable"}
    assert "secret" not in response.text
