import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.ingestion.batching import BatchExtractor, record_key
from app.ingestion.contracts import JobRecord
from app.ingestion.extraction import SkillCatalog
from app.llm.runtime import CallBudget, Decision, ModelFailure, ModelRuntime, token_usage
from app.retrieval.contracts import RankedHit
from app.retrieval.reranking import Reranker
from tests.retrieval_helpers import document


def completion(value: object) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        },
    )


@pytest.mark.parametrize(
    "response,code,split,retry",
    [
        (httpx.Response(401), "provider_auth_error", False, False),
        (
            httpx.Response(429, headers={"retry-after": "NaN"}),
            "provider_transient_error",
            False,
            True,
        ),
        (httpx.Response(503), "provider_transient_error", False, True),
        (httpx.Response(413), "provider_context_limit", True, False),
        (httpx.Response(400, json={"error": {"code": []}}), "provider_http_error", False, False),
        (
            httpx.Response(400, json={"error": {"code": "context_length_exceeded"}}),
            "provider_context_limit",
            True,
            False,
        ),
        (httpx.Response(200, content=b"invalid"), "provider_invalid_json", True, False),
        (completion([]), "provider_invalid_json", True, False),
        (httpx.Response(200, content=b"x" * 128001), "provider_response_limit", True, False),
    ],
)
async def test_transport_errors_are_classified_without_response_leaks(
    settings: Settings, response: httpx.Response, code: str, split: bool, retry: bool
) -> None:
    engine = create_async_engine(settings.database_url.get_secret_value())
    runtime = ModelRuntime(engine, settings, transport=httpx.MockTransport(lambda _: response))
    with pytest.raises(ModelFailure, match=code) as raised:
        await runtime._wire("fixture", {})
    assert raised.value.split == split
    assert raised.value.retry == retry
    await engine.dispose()


def test_usage_never_coerces_invalid_or_unknown_counts() -> None:
    assert token_usage(
        {
            "prompt_tokens": True,
            "completion_tokens": -1,
            "total_tokens": 22,
            "unexpected": "private",
        }
    ) == {"total_tokens": 22}
    assert token_usage(None) == {}


@pytest.mark.parametrize("bad", ["missing", "duplicate", "foreign", "nan", "extra"])
async def test_reranker_rejects_untrusted_output_and_preserves_baseline(
    settings: Settings, bad: str
) -> None:
    docs = [document("Python", identity=n) for n in range(1, 4)]
    hits = [RankedHit(doc.job_id, 10 - n) for n, doc in enumerate(docs)]
    items = [{"id": str(hit.job_id), "score": 0.5} for hit in hits[:2]]
    if bad == "missing":
        items.pop()
    elif bad == "duplicate":
        items[1]["id"] = items[0]["id"]
    elif bad == "foreign":
        items[1]["id"] = str(docs[2].job_id)
    elif bad == "nan":
        items[0]["score"] = float("nan")
    else:
        items[0]["extra"] = "ignore validation"
    runtime = AsyncMock()

    async def complete(task, system, user, validate, budget):
        try:
            return Decision(validate({"items": items}))
        except ValueError as exc:
            raise ModelFailure("model_schema_or_evidence_invalid") from exc

    runtime.complete.side_effect = complete
    configured = settings.model_copy(update={"rerank_enabled": True, "rerank_candidates": 2})
    result = await Reranker(runtime, configured).rerank("Python", hits, docs, {})
    assert result.hits == hits
    assert result.info.status == "fallback"


async def test_reranking_bounds_candidates_preserves_scores_tail_and_ties(
    settings: Settings,
) -> None:
    docs = [document("Python " * 1000, identity=n) for n in range(1, 5)]
    hits = [RankedHit(doc.job_id, 10 - n) for n, doc in enumerate(docs)]
    runtime = AsyncMock()

    async def complete(task, system, user, validate, budget):
        assert len(user["candidates"]) == 3
        assert all(len(item["text"]) <= 1200 for item in user["candidates"])
        return Decision(
            validate(
                {
                    "items": [
                        {"id": str(docs[n].job_id), "score": score}
                        for n, score in [(2, 0.9), (1, 0.9), (0, 0.1)]
                    ]
                }
            ),
            cached=True,
        )

    runtime.complete.side_effect = complete
    result = await Reranker(
        runtime,
        settings.model_copy(
            update={
                "rerank_enabled": True,
                "rerank_candidates": 3,
            }
        ),
    ).rerank("Python", hits, docs, {"source": "synthetic"})
    assert result.hits == [hits[1], hits[2], hits[0], hits[3]]
    assert result.info.cache_hit and result.info.status == "applied"


async def test_source_guard_and_disabled_rerank_never_reach_runtime(settings: Settings) -> None:
    docs = [replace(document("Python", identity=n), source="itviec") for n in [1, 2]]
    hits = [RankedHit(doc.job_id, 1) for doc in docs]
    runtime = AsyncMock()
    disabled = await Reranker(runtime, settings).rerank("Python", hits, docs, {})
    restricted = await Reranker(
        runtime, settings.model_copy(update={"rerank_enabled": True})
    ).rerank("Python", hits, docs, {})
    assert disabled.info.status == "disabled"
    assert restricted.info.reason == "rerank_source_restricted"
    runtime.complete.assert_not_called()


async def test_rerank_deadline_falls_back(settings: Settings) -> None:
    docs = [document("Python", identity=n) for n in [1, 2]]
    hits = [RankedHit(doc.job_id, 1) for doc in docs]
    runtime = AsyncMock()

    async def wait(*args):
        await asyncio.sleep(1)

    runtime.complete.side_effect = wait
    result = await Reranker(
        runtime,
        settings.model_copy(
            update={
                "rerank_enabled": True,
                "rerank_timeout_seconds": 0.1,
            }
        ),
    ).rerank("Python", hits, docs, {})
    assert result.hits == hits and result.info.reason == "rerank_deadline_exceeded"


async def test_batch_splitting_is_bounded_and_identity_validated(record: JobRecord) -> None:
    runtime = AsyncMock(signature="fixture")
    calls = []

    async def complete(task, system, user, validate, budget):
        if not budget.take():
            raise ModelFailure("operation_call_limit")
        budget.dispatched += 1
        calls.append(len(user["jobs"]))
        if len(user["jobs"]) > 1:
            raise ModelFailure("provider_context_limit", split=True)
        return Decision(validate({"items": [{"id": user["jobs"][0]["id"], "skills": []}]}))

    runtime.complete.side_effect = complete
    records = [record.model_copy(update={"source_job_id": str(n)}) for n in range(4)]
    report = await BatchExtractor(runtime).extract_many(records, SkillCatalog.load(), CallBudget(3))
    assert calls == [4, 2, 1]
    assert report.provider_calls == 3 and report.splits == 2
    assert len(report.results) == 4
    assert sum(item.error is None for item in report.results.values()) == 1


async def test_batch_fastpath_and_source_policy_run_before_cache(record: JobRecord) -> None:
    runtime = AsyncMock(signature="fixture")
    fast = record.model_copy(update={"structured_skills": ["Python", "Docker"]})
    private = record.model_copy(update={"source": "itviec", "source_job_id": "restricted"})
    report = await BatchExtractor(runtime).extract_many(
        [fast, private], SkillCatalog.load(), CallBudget(0)
    )
    assert report.results[record_key(fast)].fast_path
    assert report.results[record_key(private)].error == "source_local_research_only"
    runtime.complete.assert_not_called()


@pytest.mark.parametrize("kind", ["missing", "span", "foreign"])
async def test_batch_rejects_partial_ids_and_fabricated_evidence(
    record: JobRecord, kind: str
) -> None:
    runtime = AsyncMock(signature="fixture")

    async def complete(task, system, user, validate, budget):
        value = {"items": [{"id": record_key(record), "skills": []}]}
        if kind == "missing":
            value["items"] = []
        elif kind == "foreign":
            value["items"][0]["id"] = "a" * 64
        else:
            value["items"][0]["skills"] = [
                {
                    "skill": "python",
                    "source_field": "description",
                    "start": 0,
                    "end": 6,
                    "evidence": "Python",
                    "confidence": 1.0,
                }
            ]
        try:
            return Decision(validate(value))
        except ValueError as exc:
            raise ModelFailure("model_schema_or_evidence_invalid", split=True) from exc

    runtime.complete.side_effect = complete
    report = await BatchExtractor(runtime).extract_many(
        [record], SkillCatalog.load(), CallBudget(1)
    )
    assert report.results[record_key(record)].error == "model_schema_or_evidence_invalid"
