import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.core.security.demo import reserve_client
from app.db.models import ClientQuota, Job
from app.main import create_app
from tests.cv_helpers import pdf_bytes
from tests.test_demo_security import basic, secured

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Set RUN_INTEGRATION=1"),
]


async def test_client_reservations_are_atomic_persistent_and_separate_by_window(
    isolated_database: tuple[AsyncEngine, str],
) -> None:
    engine, _ = isolated_database
    results = await asyncio.gather(
        *(reserve_client(engine, "a" * 64, "minute", 3) for _ in range(10))
    )
    assert sum(results) == 3
    assert not await reserve_client(engine, "a" * 64, "minute", 3)
    assert await reserve_client(engine, "a" * 64, "llm_day", 1)
    assert not await reserve_client(engine, "a" * 64, "llm_day", 1)
    assert await reserve_client(engine, "b" * 64, "minute", 1)
    async with async_sessionmaker(engine)() as session:
        assert await session.scalar(select(func.sum(ClientQuota.used))) == 5


async def test_demo_cookie_coexists_with_cv_bearer_and_admin_writes(
    isolated_database: tuple[AsyncEngine, str],
    settings: Settings,
    record,
) -> None:
    engine, url = isolated_database
    config = secured(settings, database_url=SecretStr(url), client_limits_enabled=True)

    def check():
        with TestClient(create_app(config)) as client:
            assert client.get("/", headers=basic()).status_code == 200
            origin = {"Origin": "http://testserver"}
            payload = {"records": [record.model_dump(mode="json")]}
            assert client.post("/admin/import", headers=origin, json=payload).status_code == 403
            response = client.post(
                "/admin/import", headers={**origin, "X-Admin-Key": "a" * 64}, json=payload
            )
            assert response.status_code == 200, response.text
            assert response.json()["inserted"] == 1
            uploaded = client.post(
                "/cv", headers={**origin, "Content-Type": "application/pdf"}, content=pdf_bytes()
            )
            assert uploaded.status_code == 201, uploaded.text
            data = uploaded.json()
            path = "/cv/" + data["id"]
            bearer = {"Authorization": "Bearer " + data["access_token"]}
            assert client.get(path, headers=bearer).status_code == 200
            assert client.get(path).status_code == 404
            response = client.post(
                path + "/matches", headers={**origin, **bearer}, json={"top_k": 1}
            )
            assert response.status_code == 200, response.text
            assert client.delete(path, headers={**origin, **bearer}).status_code == 204
            assert client.get(path, headers=bearer).status_code == 404

    await asyncio.to_thread(check)
    async with async_sessionmaker(engine)() as session:
        assert await session.scalar(select(func.count()).select_from(Job)) == 1


async def test_daily_model_route_cap_stops_requests_before_planner(
    isolated_database: tuple[AsyncEngine, str],
    settings: Settings,
) -> None:
    _, url = isolated_database
    config = secured(
        settings,
        database_url=SecretStr(url),
        client_limits_enabled=True,
        agent_model_enabled=True,
        agent_daily_call_limit=10,
        client_llm_routes_per_day=0,
    )

    def check():
        with TestClient(create_app(config)) as client:
            assert client.get("/", headers=basic()).status_code == 200
            response = client.post(
                "/ask", headers={"Origin": "http://testserver"}, json={"question": "Find roles"}
            )
            assert response.status_code == 429, response.text

    await asyncio.to_thread(check)
