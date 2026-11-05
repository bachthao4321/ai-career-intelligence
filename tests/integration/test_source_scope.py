import asyncio
import os

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import HttpUrl, SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.db.models import ExtractionRun, Job, LlmDailyUsage
from app.ingestion.contracts import JobRecord
from app.ingestion.llm import DeepSeekExtractor
from app.ingestion.service import import_records
from app.main import create_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Set RUN_INTEGRATION=1"),
]


async def test_local_research_data_never_calls_external_model(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> None:
    engine, _ = isolated_database
    record = record.model_copy(
        update={
            "source": "itviec",
            "source_url": HttpUrl("https://itviec.com/it-jobs/synthetic-test-0001"),
        }
    )
    settings = settings.model_copy(
        update={
            "deepseek_api_key": SecretStr("fixture"),
            "deepseek_model": "test-model",
            "deepseek_daily_call_limit": 1,
        }
    )

    def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("Local research source must not be transmitted to an external model")

    extractor = DeepSeekExtractor(settings, transport=httpx.MockTransport(handler))
    report = await import_records(engine, [record], settings, extractor=extractor, max_calls=1)
    assert report.llm_calls == 0
    assert report.fallback == 1
    assert report.accepted_skills == 4
    async with async_sessionmaker(engine)() as session:
        assert await session.scalar(select(func.count()).select_from(LlmDailyUsage)) == 0
        result = await session.scalar(select(ExtractionRun.result))
        assert result["error"] == "source_local_research_only"


async def test_production_hides_restricted_sources_in_list_counts_and_details(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> None:
    engine, url = isolated_database
    private = record.model_copy(
        update={
            "source": "itviec",
            "source_url": HttpUrl("https://itviec.com/it-jobs/synthetic-test-0001"),
        }
    )
    alternate_name = record.model_copy(
        update={
            "source_job_id": "private-2",
            "source_url": HttpUrl("https://user@www.itviec.com:443/it-jobs/synthetic-test-0002"),
        }
    )
    await import_records(engine, [record, private, alternate_name], settings)
    async with async_sessionmaker(engine)() as session:
        private_ids = (
            await session.scalars(select(Job.id).where(Job.source_url != str(record.source_url)))
        ).all()

    def check() -> None:
        for environment, total in (("local", 3), ("production", 1)):
            config = settings.model_copy(
                update={"database_url": SecretStr(url), "app_env": environment}
            )
            with TestClient(create_app(config)) as client:
                page = client.get("/jobs")
                assert page.status_code == 200, page.text
                assert page.json()["total"] == total
                for job_id in private_ids:
                    detail = client.get(f"/jobs/{job_id}")
                    assert detail.status_code == (200 if environment == "local" else 404)
                    if environment == "local":
                        assert detail.json()["usage_scope"] == "local_research_only"

    await asyncio.to_thread(check)
