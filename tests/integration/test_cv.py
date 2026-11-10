import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.cv.contracts import CVError, MatchRequest
from app.cv.service import CVCapacityReached, CVNotFound, CVService
from app.db.models import CVDocument, Job, JobSkill
from app.ingestion.contracts import JobRecord
from app.ingestion.service import import_records
from app.main import create_app
from tests.cv_helpers import pdf_bytes

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Set RUN_INTEGRATION=1"),
]


@dataclass
class Harness:
    engine: AsyncEngine
    settings: Settings
    service: CVService


@pytest.fixture
async def cv(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> AsyncIterator[Harness]:
    engine, url = isolated_database
    config = settings.model_copy(update={"database_url": SecretStr(url), "app_env": "test"})
    public = record.model_copy(
        update={
            "source": "jobtech",
            "description": "Python and Docker for backend service development.",
        }
    )
    duplicate = public.model_copy(update={"source_job_id": "duplicate"})
    private = public.model_copy(update={"source": "itviec", "source_job_id": "private"})
    await import_records(
        engine, [public, duplicate, private], config.model_copy(update={"app_env": "local"})
    )
    yield Harness(engine, config, CVService(engine, config))


async def test_api_upload_match_and_delete_with_private_source_isolation(
    cv: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_model = AsyncMock(side_effect=AssertionError("CV must not call chat models"))
    monkeypatch.setattr("app.agents.planner.ModelPlanner.plan", no_model)

    def exercise() -> None:
        with TestClient(create_app(cv.settings)) as client:
            response = client.post(
                "/cv", content=pdf_bytes(), headers={"content-type": "application/pdf"}
            )
            assert response.status_code == 201, response.text
            body = response.json()
            token = body["access_token"]
            path = "/cv/" + body["id"]
            headers = {"Authorization": "Bearer " + token}
            assert response.headers["cache-control"] == "no-store"
            assert len(token) >= 32
            get = client.get(path, headers=headers)
            assert get.status_code == 200 and "access_token" not in get.json()
            assert client.get(path).status_code == 404
            wrong = {"Authorization": "Bearer " + "x" * 43}
            assert client.get(path, headers=wrong).status_code == 404
            assert client.delete(path, headers=wrong).status_code == 404
            assert client.post(path + "/matches", json={}, headers=wrong).status_code == 404
            response = client.post(path + "/matches", json={}, headers=headers)
            assert response.status_code == 200, response.text
            report = response.json()
            assert report["eligible_listings"] == 2 and report["unique_documents"] == 1
            assert report["items"][0]["source"] == "jobtech"
            assert report["items"][0]["duplicate_listings"] == 2
            item = report["items"][0]
            assert [s["slug"] for s in item["matched_skills"]] == ["python"]
            assert [s["slug"] for s in item["skills_not_seen_in_cv"]] == ["docker"]
            assert item["skills_not_seen_in_cv"][0]["job_evidence"]["text"] == "Docker"
            assert (
                client.post(
                    path + "/matches", json={"filters": {"source": "itviec"}}, headers=headers
                ).json()["items"]
                == []
            )
            assert client.delete(path, headers=headers).status_code == 204
            assert client.get(path, headers=headers).status_code == 404
            assert client.post(path + "/matches", json={}, headers=headers).status_code == 404

    await asyncio.to_thread(exercise)
    no_model.assert_not_called()
    async with cv.engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(CVDocument)) == 0


async def test_only_hash_of_capability_is_stored_and_tokens_do_not_cross_profiles(
    cv: Harness,
) -> None:
    first = await cv.service.upload(pdf_bytes())
    second = await cv.service.upload(pdf_bytes())
    assert first.id != second.id and first.access_token != second.access_token
    async with cv.engine.connect() as conn:
        row = (
            (await conn.execute(select(CVDocument).where(CVDocument.id == first.id)))
            .mappings()
            .one()
        )
        assert row["token_hash"] != first.access_token
        assert first.access_token not in str(dict(row))
        assert set(row) == {"id", "token_hash", "profile", "created_at", "expires_at"}
        assert (first.expires_at - first.created_at).total_seconds() == 3600
    with pytest.raises(CVNotFound):
        await cv.service.get(first.id, second.access_token)
    with pytest.raises(CVNotFound):
        await cv.service.delete(first.id, second.access_token)


async def test_expiry_blocks_reads_and_reports_before_sweep_then_purges(cv: Harness) -> None:
    upload = await cv.service.upload(pdf_bytes())
    async with cv.engine.begin() as conn:
        await conn.execute(
            update(CVDocument).values(expires_at=func.now() - text("INTERVAL '1 second'"))
        )
    with pytest.raises(CVNotFound):
        await cv.service.get(upload.id, upload.access_token)
    with pytest.raises(CVNotFound):
        await cv.service.match(upload.id, upload.access_token, MatchRequest())
    assert await cv.service.cleanup() == 1
    assert await cv.service.cleanup() == 0


async def test_capacity_is_atomic_across_service_instances_and_expired_rows_free_space(
    cv: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = (await cv.service.upload(pdf_bytes())).profile
    await cv.service.cleanup()
    async with cv.engine.begin() as conn:
        await conn.execute(CVDocument.__table__.delete())
    monkeypatch.setattr("app.cv.service.parse_pdf", AsyncMock(return_value=profile))
    config = cv.settings.model_copy(update={"cv_capacity": 1})
    one, two = CVService(cv.engine, config), CVService(cv.engine, config)
    result = await asyncio.gather(
        one.upload(b"fixture"), two.upload(b"fixture"), return_exceptions=True
    )
    assert sum(isinstance(r, CVCapacityReached) for r in result) == 1
    async with cv.engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(CVDocument)) == 1
    async with cv.engine.begin() as conn:
        await conn.execute(
            update(CVDocument).values(expires_at=func.now() - text("INTERVAL '1 second'"))
        )
    assert (await one.upload(b"fixture")).profile == profile


async def test_parse_failure_persists_nothing(cv: Harness) -> None:
    with pytest.raises(CVError):
        await cv.service.upload(b"%PDF-1.7 malformed private data")
    async with cv.engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(CVDocument)) == 0


async def test_stale_and_tampered_job_evidence_never_become_gap_claims(cv: Harness) -> None:
    upload = await cv.service.upload(pdf_bytes())
    async with cv.engine.begin() as conn:
        await conn.execute(
            update(JobSkill).where(JobSkill.skill_slug == "docker").values(start_offset=1)
        )
    report = await cv.service.match(upload.id, upload.access_token, MatchRequest())
    assert report.items[0].skills_not_seen_in_cv == []
    assert "some_job_skill_evidence_invalid_excluded_from_comparison" in report.warnings
    async with cv.engine.begin() as conn:
        await conn.execute(update(Job).values(content_hash="a" * 64))
    report = await cv.service.match(upload.id, upload.access_token, MatchRequest())
    assert all(
        not item.matched_skills and not item.skills_not_seen_in_cv and item.skill_coverage is None
        for item in report.items
    )


async def test_delete_waits_for_authorized_inflight_access(cv: Harness) -> None:
    upload = await cv.service.upload(pdf_bytes())
    async with cv.service.access(upload.id, upload.access_token):
        deleting = asyncio.create_task(cv.service.delete(upload.id, upload.access_token))
        await asyncio.sleep(0.05)
        assert not deleting.done()
    await deleting
    with pytest.raises(CVNotFound):
        await cv.service.get(upload.id, upload.access_token)


async def test_expiry_checked_again_after_inflight_processing(cv: Harness) -> None:
    upload = await cv.service.upload(pdf_bytes())
    async with cv.engine.begin() as conn:
        await conn.execute(
            update(CVDocument).values(
                expires_at=func.clock_timestamp() + text("INTERVAL '0.5 seconds'")
            )
        )
    with pytest.raises(CVNotFound):
        async with cv.service.access(upload.id, upload.access_token):
            await asyncio.sleep(0.6)


async def test_match_concurrency_limit_fails_before_reading_cv(cv: Harness) -> None:
    cv.service.match_slots = asyncio.Semaphore(0)
    with pytest.raises(CVCapacityReached, match="cv_matching_busy"):
        await cv.service.match(uuid.uuid4(), "x" * 43, MatchRequest())


async def test_background_sweep_removes_expired_rows_without_a_profile_request(cv: Harness) -> None:
    upload = await cv.service.upload(pdf_bytes())
    async with cv.engine.begin() as conn:
        await conn.execute(
            update(CVDocument).values(expires_at=func.now() - text("INTERVAL '1 second'"))
        )
    # Accelerate the scheduler only in this fixture; public config has a five-second minimum.
    service = CVService(
        cv.engine, cv.settings.model_copy(update={"cv_cleanup_interval_seconds": 0.01})
    )
    purged = asyncio.Event()
    original = service.cleanup

    async def observe() -> int:
        removed = await original()
        if removed:
            purged.set()
        return removed

    service.cleanup = observe  # type: ignore[method-assign]
    task = asyncio.create_task(service.cleanup_loop())
    try:
        await asyncio.wait_for(purged.wait(), 2)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    with pytest.raises(CVNotFound):
        await cv.service.get(upload.id, upload.access_token)
