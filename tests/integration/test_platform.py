import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from pydantic import HttpUrl, SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.ingestion.service import import_records
from app.main import create_app
from app.schemas.ingestion import JobRecord
from tests.test_demo_security import basic, secured

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Set RUN_INTEGRATION=1"),
]


async def test_platform_scoped_overview_feed_filters_and_dedup(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> None:
    engine, url = isolated_database
    config = settings.model_copy(update={"database_url": SecretStr(url), "app_env": "local"})
    first = record.model_copy(update={"source": "jobtech"})
    duplicate = first.model_copy(update={"source_job_id": "duplicate"})
    private = first.model_copy(update={"source": "itviec", "source_job_id": "private"})
    renamed = first.model_copy(
        update={
            "source": "renamed",
            "source_job_id": "hidden",
            "source_url": HttpUrl("https://www.topcv.vn/viec-lam/123"),
        }
    )
    await import_records(engine, [first, duplicate, private, renamed], config)

    def exercise(environment: str, expected: int) -> None:
        scoped = config.model_copy(update={"app_env": environment})
        if environment == "production":
            scoped = secured(scoped, client_limits_enabled=True, public_origin="https://testserver")
        with TestClient(
            create_app(scoped),
            base_url="https://testserver",
            headers=basic() if environment == "production" else {},
        ) as client:
            response = client.get("/platform/overview")
            assert response.status_code == 200, response.text
            overview = response.json()
            assert overview["listings"] == expected and overview["unique_documents"] == 1
            assert next(s["count"] for s in overview["skills"] if s["slug"] == "python") == expected
            assert {s["source"] for s in overview["sources"]} == (
                {"jobtech", "itviec", "renamed"} if environment == "local" else {"jobtech"}
            )
            assert overview["cv_max_bytes"] == 2 * 1024 * 1024
            assert "database_url" not in overview
            response = client.get("/platform/jobs?limit=1&skills=python&location=Hồ Chí Minh")
            assert response.status_code == 200, response.text
            page = response.json()
            assert page["total_listings"] == expected and page["total"] == 1
            assert len(page["items"]) == 1 and page["items"][0]["duplicate_listings"] == expected
            assert "python" in page["items"][0]["skills"]
            assert client.get("/platform/jobs?limit=1&offset=1").json()["items"] == []
            assert client.get("/platform/jobs?location=%25").json()["items"] == []
            assert client.get("/platform/jobs?workplace_type=remote").json()["items"] == []
            assert client.get("/platform/jobs?limit=1000").status_code == 422
            assert client.get("/platform/jobs?source=invalid%20source").status_code == 422
            assert client.get("/platform/jobs?sql=SELECT").status_code == 422
            if environment != "local":
                assert page["items"][0]["source"] == "jobtech"
                assert client.get("/platform/jobs?source=itviec").json()["items"] == []

    await asyncio.to_thread(exercise, "local", 4)
    await asyncio.to_thread(exercise, "production", 2)
