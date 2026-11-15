import json
import os

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.db.models import Job
from app.ingestion.service import import_records
from app.schemas.ingestion import JobRecord
from app.services.translation.presentation import presentations
from app.services.translation.reviewed_titles import REVIEWED_TITLES
from app.services.translation.titles import translate_titles

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Set RUN_INTEGRATION=1"),
]


async def test_translation_is_cached_and_invalidated_without_changing_original_evidence(
    isolated_database: tuple[AsyncEngine, str],
    settings: Settings,
    record: JobRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, _ = isolated_database
    monkeypatch.delitem(REVIEWED_TITLES, record.title, raising=False)
    await import_records(engine, [record], settings)
    config = settings.model_copy(
        update={
            "agent_model_enabled": True,
            "agent_daily_call_limit": 3,
            "llm_min_interval_seconds": 0,
        }
    )
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        items = json.loads(body["messages"][1]["content"])["items"]
        assert all(set(item) == {"id", "title"} for item in items)
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "items": [
                                        {
                                            "id": item["id"],
                                            "en": "Backend Developer",
                                            "vi": "Lập trình viên backend",
                                        }
                                        for item in items
                                    ]
                                }
                            )
                        },
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handle)
    first = await translate_titles(engine, config, max_calls=2, transport=transport)
    second = await translate_titles(engine, config, max_calls=2, transport=transport)
    assert first.translated == 1 and first.calls == 1
    assert second.cached == 1 and second.calls == 0
    assert len(calls) == 1
    async with async_sessionmaker(engine)() as session:
        job = (await session.scalars(select(Job))).one()
        assert job.title == record.title and job.description == record.description
        value = (await presentations(session, [job], config))[str(job.id)]
        # Machine output remains cached but cannot be displayed without review.
        assert value.title_vi is None
        assert value.original_title == record.title
        assert value.source_excerpt == record.description[:240]
        monkeypatch.setitem(
            REVIEWED_TITLES, record.title, ("Backend Developer", "Lập trình viên Backend")
        )
        assert (await presentations(session, [job], config))[
            str(job.id)
        ].title_vi == "Lập trình viên Backend"
        # Human-reviewed titles are independent of the model used for draft translations.
        assert (
            await presentations(
                session, [job], config.model_copy(update={"agent_model_name": "new-model"})
            )
        )[str(job.id)].title_vi == "Lập trình viên Backend"
        await session.execute(update(Job).where(Job.id == job.id).values(title="New title"))
        await session.commit()
    async with async_sessionmaker(engine)() as session:
        job = (await session.scalars(select(Job))).one()
        assert (await presentations(session, [job], config))[str(job.id)].title_vi is None


async def test_http_failure_does_not_split_or_retry_private_titles_to_hosted_model(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> None:
    engine, _ = isolated_database
    private = record.model_copy(update={"source": "itviec", "source_job_id": "private"})
    await import_records(engine, [record, private], settings)
    config = settings.model_copy(
        update={
            "agent_model_enabled": True,
            "agent_daily_call_limit": 3,
            "llm_min_interval_seconds": 0,
            "agent_model_base_url": "https://provider.example/v1",
            "app_env": "local",
        }
    )
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401)

    result = await translate_titles(
        engine, config, max_calls=3, batch_size=1, transport=httpx.MockTransport(handle)
    )
    assert result.restricted_skipped == 1
    assert result.calls == result.failed_batches == len(calls) == 1
    assert result.translated == 0
