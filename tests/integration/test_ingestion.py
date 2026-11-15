import asyncio
import json
import os
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    AliasCandidate,
    ExtractionRun,
    Job,
    JobSkill,
    JobSnapshot,
    LlmDailyUsage,
    SkillAlias,
)
from app.ingestion.model_extraction import DeepSeekExtractor
from app.ingestion.reporting import build_report
from app.ingestion.service import import_records, review_alias
from app.main import create_app
from app.repositories.quotas import reserve_daily_call
from app.schemas.ingestion import JobRecord

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Set RUN_INTEGRATION=1"),
]


async def test_reruns_updates_reverts_and_snapshots_are_consistent(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> None:
    engine, _ = isolated_database
    first = await import_records(engine, [record], settings)
    second = await import_records(engine, [record], settings)
    changed = record.model_copy(update={"description": "Docker"})
    third = await import_records(engine, [changed], settings)
    fourth = await import_records(engine, [record], settings)
    assert first.inserted == first.extracted == 1
    assert second.unchanged == second.extraction_cached == 1
    assert third.updated == third.extracted == 1
    assert fourth.updated == fourth.extraction_cached == 1
    async with async_sessionmaker(engine)() as session:
        assert await session.scalar(select(func.count()).select_from(Job)) == 1
        assert await session.scalar(select(func.count()).select_from(JobSnapshot)) == 2
        assert await session.scalar(select(func.count()).select_from(ExtractionRun)) == 2
        assert set(await session.scalars(select(JobSkill.skill_slug))) == {
            "python",
            "postgresql",
            "kubernetes",
            "javascript",
        }
    report = await build_report(engine)
    assert report["literal_evidence_errors"] == []
    assert report["jobs"] == 1


async def test_concurrent_imports_do_not_duplicate_jobs_or_runs(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> None:
    engine, _ = isolated_database
    await asyncio.gather(*(import_records(engine, [record], settings) for _ in range(3)))
    report = await build_report(engine)
    assert report["jobs"] == report["snapshots"] == report["extraction_runs"] == 1


async def test_daily_call_reservations_are_atomic_and_persistent(
    isolated_database: tuple[AsyncEngine, str],
) -> None:
    engine, _ = isolated_database
    granted = await asyncio.gather(*(reserve_daily_call(engine, 3) for _ in range(10)))
    assert sum(granted) == 3
    assert await reserve_daily_call(engine, 3) is False
    assert await reserve_daily_call(engine, 0) is False
    async with async_sessionmaker(engine)() as session:
        assert await session.scalar(select(LlmDailyUsage.calls)) == 3


async def test_llm_fallback_is_cached_and_budget_attempt_is_not_refunded(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> None:
    engine, _ = isolated_database
    settings = settings.model_copy(
        update={
            "deepseek_api_key": SecretStr("fixture"),
            "deepseek_model": "test-model",
            "deepseek_daily_call_limit": 1,
        }
    )
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429)

    extractor = DeepSeekExtractor(settings, transport=httpx.MockTransport(handler))
    report = await import_records(engine, [record], settings, extractor=extractor, max_calls=1)
    assert report.fallback == report.llm_calls == calls == 1
    again = await import_records(engine, [record], settings, extractor=extractor, max_calls=1)
    assert again.extraction_cached == 1
    retried = await import_records(
        engine, [record], settings, extractor=extractor, max_calls=1, retry_failed=True
    )
    assert retried.fallback == 1
    assert retried.llm_calls == 0
    assert calls == 1
    assert (await build_report(engine))["skill_assignments"] == 4


async def test_llm_aliases_require_review_and_cache_invalidates_after_approval(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> None:
    engine, _ = isolated_database
    record = record.model_copy(update={"description": "Kube"})
    settings = settings.model_copy(
        update={
            "deepseek_api_key": SecretStr("fixture"),
            "deepseek_model": "test-model",
            "deepseek_daily_call_limit": 1,
        }
    )
    payload = {
        "skills": [
            {
                "skill": "kubernetes",
                "evidence": "Kube",
                "source_field": "description",
                "start": 0,
                "end": 4,
                "confidence": 0.99,
            }
        ]
    }
    extractor = DeepSeekExtractor(
        settings,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(payload)}}
                    ]
                },
            )
        ),
    )
    report = await import_records(engine, [record], settings, extractor=extractor, max_calls=1)
    assert report.alias_candidates == 1
    assert report.accepted_skills == 0
    async with async_sessionmaker(engine)() as session:
        candidate_id = await session.scalar(select(AliasCandidate.id))
        assert await session.get(SkillAlias, "kube") is None
    await review_alias(engine, candidate_id, "approve", "synthetic-test-reviewer")
    next_run = await import_records(engine, [record], settings)
    assert next_run.accepted_skills == 1
    async with async_sessionmaker(engine)() as session:
        assert await session.scalar(select(JobSkill.skill_slug)) == "kubernetes"
        stored = await session.get(SkillAlias, "kube")
        assert stored.origin == "reviewed"
    with pytest.raises(ValueError, match="already reviewed"):
        await review_alias(engine, candidate_id, "approve", "synthetic-test-reviewer")


async def test_jobs_api_paginates_and_returns_evidence_without_raw_payload(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> None:
    engine, url = isolated_database
    await import_records(engine, [record], settings)
    async with async_sessionmaker(engine)() as session:
        job_id = await session.scalar(select(Job.id))

    def check() -> None:
        with TestClient(
            create_app(settings.model_copy(update={"database_url": SecretStr(url)}))
        ) as client:
            page = client.get("/jobs?limit=1")
            assert page.status_code == 200, page.text
            assert page.json()["total"] == 1
            assert "raw_payload" not in page.text
            detail = client.get(f"/jobs/{job_id}")
            assert detail.status_code == 200, detail.text
            assert len(detail.json()["skills"]) == 4
            assert detail.json()["source"] == "synthetic"
            assert "source_url" in detail.json()
            assert client.get("/jobs?limit=101").status_code == 422
            assert client.get("/jobs?offset=-1").status_code == 422
            assert client.get(f"/jobs/{uuid.uuid4()}").status_code == 404

    await asyncio.to_thread(check)
