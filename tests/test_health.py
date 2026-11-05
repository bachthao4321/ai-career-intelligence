import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api.health import get_probes
from app.core.config import Settings
from app.main import create_app


class FakeProbes:
    def __init__(self, postgres_ok: bool, qdrant_ok: bool) -> None:
        self.postgres_ok = postgres_ok
        self.qdrant_ok = qdrant_ok

    async def postgres(self) -> None:
        if not self.postgres_ok:
            raise RuntimeError("postgresql://admin:do-not-expose@private-host/db")

    async def qdrant(self) -> None:
        if not self.qdrant_ok:
            raise RuntimeError("provider-token-do-not-expose")


def test_health_does_not_require_dependencies(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "postgres_ok,qdrant_ok", [(True, True), (False, True), (True, False), (False, False)]
)
def test_readiness_reports_dependencies_without_secrets(
    settings: Settings, postgres_ok: bool, qdrant_ok: bool, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(settings)
    app.dependency_overrides[get_probes] = lambda: FakeProbes(postgres_ok, qdrant_ok)
    with TestClient(app) as client:
        response = client.get("/ready")
    ready = postgres_ok and qdrant_ok
    assert response.status_code == (200 if ready else 503)
    assert response.json() == {
        "status": "ready" if ready else "not_ready",
        "dependencies": {
            "postgres": "ok" if postgres_ok else "unavailable",
            "qdrant": "ok" if qdrant_ok else "unavailable",
        },
    }
    assert response.headers["cache-control"] == "no-store"
    assert "do-not-expose" not in response.text + caplog.text
    assert "private-host" not in response.text + caplog.text


def test_readiness_bounds_a_hanging_dependency(settings: Settings) -> None:
    class HangingPostgres(FakeProbes):
        async def postgres(self) -> None:
            await asyncio.Event().wait()

    app = create_app(settings)
    app.dependency_overrides[get_probes] = lambda: HangingPostgres(True, True)
    with TestClient(app) as client:
        response = client.get("/ready")
        assert client.get("/health").status_code == 200
    assert response.status_code == 503
    assert response.json()["dependencies"] == {"postgres": "unavailable", "qdrant": "ok"}
