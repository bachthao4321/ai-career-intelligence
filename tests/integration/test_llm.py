import asyncio
import json
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.db.models import ExtractionRun, LlmAttempt, LlmDailyUsage, LlmDecision
from app.ingestion.batching import BatchExtractor
from app.ingestion.service import import_records
from app.integrations.llm.runtime import CallBudget, ModelFailure, ModelRuntime
from app.main import create_app
from app.schemas.ingestion import JobRecord
from tests.retrieval_helpers import TestEmbedder
from tests.test_model_runtime import completion

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Set RUN_INTEGRATION=1"),
]


def config(settings: Settings, **overrides) -> Settings:
    return settings.model_copy(
        update={
            "llm_daily_call_limit": 20,
            "llm_min_interval_seconds": 0,
            "llm_timeout_seconds": 2,
            **overrides,
        }
    )


def validate(value):
    if value != {"ok": True}:
        raise ValueError("invalid fixture")
    return value


async def test_retries_cache_restart_and_revision_invalidation(
    isolated_database: tuple[AsyncEngine, str],
    settings: Settings,
) -> None:
    engine, _ = isolated_database
    calls = 0

    def handler(_):
        nonlocal calls
        calls += 1
        return httpx.Response(429) if calls == 1 else completion({"ok": True})

    transport = httpx.MockTransport(handler)
    configured = config(settings)
    runtime = ModelRuntime(engine, configured, transport=transport)
    first = await runtime.complete("rerank-v1", "fixture", {}, validate, CallBudget(2))
    assert first.attempts == 2 and first.usage["total_tokens"] == 16
    restarted = ModelRuntime(engine, configured, transport=transport)
    second = await restarted.complete("rerank-v1", "fixture", {}, validate, CallBudget(0))
    assert second.cached and calls == 2
    changed = ModelRuntime(
        engine, config(settings, llm_model_revision="different"), transport=transport
    )
    assert not (await changed.complete("rerank-v1", "fixture", {}, validate, CallBudget(1))).cached
    async with async_sessionmaker(engine)() as session:
        assert await session.scalar(select(LlmDailyUsage.calls)) == calls == 3
        attempts = (await session.scalars(select(LlmAttempt))).all()
        assert {a.status for a in attempts} == {"ok", "provider_transient_error"}
        assert all(a.elapsed_ms >= 0 for a in attempts)


async def test_concurrent_workers_obey_shared_quota_and_concurrency(
    isolated_database: tuple[AsyncEngine, str],
    settings: Settings,
) -> None:
    engine, _ = isolated_database
    calls = 0

    async def handler(_):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return completion({"ok": True})

    configured = config(settings, llm_daily_call_limit=2, llm_concurrency=4)
    workers = [
        ModelRuntime(engine, configured, transport=httpx.MockTransport(handler)) for _ in range(4)
    ]
    results = await asyncio.gather(
        *[
            worker.complete("rerank-v1", "fixture", {"i": i}, validate, CallBudget(1))
            for i, worker in enumerate(workers)
        ],
        return_exceptions=True,
    )
    assert calls == 2
    assert sum(not isinstance(result, Exception) for result in results) == 2
    async with async_sessionmaker(engine)() as session:
        assert await session.scalar(select(LlmDailyUsage.calls)) == 2


async def test_singleflight_cancel_cleanup_expired_lease_and_global_rate(
    isolated_database: tuple[AsyncEngine, str],
    settings: Settings,
) -> None:
    engine, _ = isolated_database
    entered = asyncio.Event()
    hold = asyncio.Event()
    calls = 0

    async def handler(_):
        nonlocal calls
        calls += 1
        entered.set()
        await hold.wait()
        return completion({"ok": True})

    runtime = ModelRuntime(
        engine, config(settings, llm_concurrency=1), transport=httpx.MockTransport(handler)
    )
    task = asyncio.create_task(
        runtime.complete("rerank-v1", "fixture", {}, validate, CallBudget(1))
    )
    await asyncio.wait_for(entered.wait(), 1)
    other = ModelRuntime(engine, runtime.settings, transport=runtime.transport)
    with pytest.raises(ModelFailure, match="decision_in_progress"):
        await other.complete("rerank-v1", "fixture", {}, validate, CallBudget(1))
    with pytest.raises(ModelFailure, match="provider_busy"):
        await other.complete("rerank-v1", "fixture", {"different": True}, validate, CallBudget(1))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with async_sessionmaker(engine)() as session:
        attempt = await session.scalar(select(LlmAttempt))
        assert attempt.status == "cancelled"
    # A process killed without finalization leaves a lease; expiry permits recovery.
    async with async_sessionmaker(engine).begin() as session:
        await session.execute(
            update(LlmAttempt).values(
                status="running", lease_until=datetime.now(UTC) - timedelta(seconds=1)
            )
        )
    hold.set()
    assert not (await other.complete("rerank-v1", "fixture", {}, validate, CallBudget(1))).cached
    rate_limited = ModelRuntime(
        engine, config(settings, llm_min_interval_seconds=0.2), transport=runtime.transport
    )
    start = asyncio.get_running_loop().time()
    await rate_limited.complete(
        "rerank-v1", "fixture", {"different": True}, validate, CallBudget(1)
    )
    assert asyncio.get_running_loop().time() - start >= 0.15
    assert calls == 3


async def test_cache_revalidation_expiry_and_capacity(
    isolated_database: tuple[AsyncEngine, str],
    settings: Settings,
) -> None:
    engine, _ = isolated_database
    calls = 0

    def handler(_):
        nonlocal calls
        calls += 1
        return completion({"ok": True})

    runtime = ModelRuntime(
        engine, config(settings, llm_cache_capacity=1), transport=httpx.MockTransport(handler)
    )

    async def run(value):
        return await runtime.complete("rerank-v1", "fixture", value, validate, CallBudget(1))

    await run({})
    async with async_sessionmaker(engine).begin() as session:
        await session.execute(update(LlmDecision).values(value={"ok": False}))
    assert not (await run({})).cached
    await run({"other": True})
    async with async_sessionmaker(engine).begin() as session:
        assert await session.scalar(select(func.count()).select_from(LlmDecision)) == 1
        await session.execute(
            update(LlmDecision).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert not (await run({})).cached
    assert calls == 4


@pytest.mark.parametrize(
    "status,expected", [(401, "provider_auth_error"), (429, "provider_transient_error")]
)
async def test_failures_stop_at_operation_budget_without_refunding(
    isolated_database: tuple[AsyncEngine, str],
    settings: Settings,
    status: int,
    expected: str,
) -> None:
    engine, _ = isolated_database
    calls = 0

    def handler(_):
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    runtime = ModelRuntime(engine, config(settings), transport=httpx.MockTransport(handler))
    budget = CallBudget(1)
    with pytest.raises(ModelFailure, match=expected if status == 401 else "operation_call_limit"):
        await runtime.complete("rerank-v1", "fixture", {}, validate, budget)
    assert calls == budget.dispatched == 1
    async with async_sessionmaker(engine)() as session:
        assert await session.scalar(select(LlmDailyUsage.calls)) == 1


async def test_batch_import_splits_falls_back_and_resumes_without_calls(
    isolated_database: tuple[AsyncEngine, str],
    settings: Settings,
    record: JobRecord,
) -> None:
    engine, _ = isolated_database
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        jobs = json.loads(json.loads(request.content)["messages"][1]["content"])["jobs"]
        if len(jobs) > 1:
            return httpx.Response(413)
        return completion({"items": [{"id": jobs[0]["id"], "skills": []}]})

    configured = config(settings)
    extractor = BatchExtractor(
        ModelRuntime(engine, configured, transport=httpx.MockTransport(handler))
    )
    records = [record.model_copy(update={"source_job_id": str(n)}) for n in range(3)]
    report = await import_records(
        engine, records, configured, batch_extractor=extractor, max_calls=3
    )
    assert report.llm_calls == calls == 3
    assert report.batch_splits == 1 and report.fallback == 2
    again = await import_records(
        engine, records, configured, batch_extractor=extractor, max_calls=3
    )
    assert again.extraction_cached == 3 and calls == 3
    async with async_sessionmaker(engine)() as session:
        assert await session.scalar(select(func.count()).select_from(ExtractionRun)) == 3


async def test_search_api_rerank_is_opt_in_and_validated_cache_is_reused(
    isolated_database: tuple[AsyncEngine, str],
    settings: Settings,
    record: JobRecord,
) -> None:
    engine, url = isolated_database
    configured = config(settings, database_url=SecretStr(url), rerank_enabled=True)
    records = [
        record.model_copy(update={"source_job_id": str(n), "description": f"Python {n}"})
        for n in range(3)
    ]
    await import_records(engine, records, configured)
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        candidates = json.loads(json.loads(request.content)["messages"][1]["content"])["candidates"]
        return completion(
            {
                "items": [
                    {"id": candidate["id"], "score": n / 10}
                    for n, candidate in enumerate(candidates)
                ]
            }
        )

    def check():
        with TestClient(create_app(configured, embedder=TestEmbedder())) as client:
            client.app.state.services.search.reranker.runtime.transport = httpx.MockTransport(
                handler
            )
            baseline = client.post("/search", json={"query": "Python"}).json()
            assert baseline["rerank"]["status"] == "not_requested" and calls == 0
            response = client.post("/search", json={"query": "Python", "rerank": True})
            assert response.status_code == 200
            ranked = response.json()
            assert ranked["rerank"]["status"] == "applied" and calls == 1
            assert [hit["job_id"] for hit in ranked["items"]] == list(
                reversed([hit["job_id"] for hit in baseline["items"]])
            )
            assert {hit["job_id"]: hit["score"] for hit in ranked["items"]} == {
                hit["job_id"]: hit["score"] for hit in baseline["items"]
            }
            again = client.post("/search", json={"query": "Python", "rerank": True}).json()
            assert again["rerank"]["cache_hit"] and calls == 1

    await asyncio.to_thread(check)
