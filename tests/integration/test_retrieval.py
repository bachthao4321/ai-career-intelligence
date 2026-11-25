import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import HttpUrl, SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.db.models import IndexingRun, Job, VectorIndexState
from app.ingestion.service import import_records
from app.integrations.qdrant import QdrantStore, VectorBackendUnavailable
from app.main import create_app
from app.schemas.ingestion import JobRecord
from app.schemas.search import SearchFilters, SearchRequest
from app.services.search.indexing import IndexAlreadyRunning, index_documents, namespace
from app.services.search.service import DenseSearchUnavailable, SearchService, SimilarJobNotFound
from tests.retrieval_helpers import TestEmbedder

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Set RUN_INTEGRATION=1"),
]


@dataclass
class Harness:
    engine: AsyncEngine
    settings: Settings
    embedder: TestEmbedder
    store: QdrantStore
    service: SearchService


@pytest.fixture
async def harness(
    isolated_database: tuple[AsyncEngine, str], settings: Settings
) -> AsyncIterator[Harness]:
    engine, url = isolated_database
    config = settings.model_copy(
        update={
            "database_url": SecretStr(url),
            "app_env": "local",
            "qdrant_url": Settings().qdrant_url,
            "qdrant_collection_prefix": "test_m2_" + uuid.uuid4().hex[:20],
        }
    )
    encoder = TestEmbedder()
    async with httpx.AsyncClient(base_url=config.qdrant_url, timeout=30) as client:
        store = QdrantStore(client, namespace(config, encoder), encoder.dimension)
        try:
            yield Harness(
                engine, config, encoder, store, SearchService(engine, config, encoder, store)
            )
        finally:
            # These exact UUID-namespaced test collections belong to this fixture only.
            await client.delete(f"/collections/{store.collection}")
            public = config.model_copy(update={"app_env": "production"})
            await client.delete(f"/collections/{namespace(public, encoder)}")


async def test_index_is_idempotent_and_deduplicates_before_retrieval(
    harness: Harness, record: JobRecord
) -> None:
    h = harness
    duplicate = record.model_copy(update={"source_job_id": "job-2"})
    await import_records(h.engine, [record, duplicate], h.settings)
    first = await index_documents(h.engine, h.settings, h.embedder, h.store)
    calls = h.embedder.calls
    second = await index_documents(h.engine, h.settings, h.embedder, h.store)
    assert first.indexed == second.skipped == 2
    assert second.indexed == 0
    assert h.embedder.calls == calls
    for mode in ("bm25", "dense", "hybrid"):
        response = await h.service.search(SearchRequest(query="Python", mode=mode))
        assert response.effective_mode == mode
        assert response.eligible_listings == 2
        assert response.unique_documents == len(response.items) == 1
        assert response.items[0].duplicate_listings == 2


async def test_updates_do_not_serve_stale_vectors_and_deletion_reconciles(
    harness: Harness, record: JobRecord
) -> None:
    h = harness
    await import_records(h.engine, [record], h.settings)
    await index_documents(h.engine, h.settings, h.embedder, h.store)
    original_ids = await h.store.managed_ids()
    changed = record.model_copy(update={"description": "Java enterprise development"})
    await import_records(h.engine, [changed], h.settings)
    with pytest.raises(DenseSearchUnavailable):
        await h.service.search(SearchRequest(query="Java", mode="dense"))
    fallback = await h.service.search(SearchRequest(query="Java", mode="hybrid"))
    assert fallback.effective_mode == "bm25"
    assert "dense_index_not_ready" in fallback.warnings
    updated = await index_documents(h.engine, h.settings, h.embedder, h.store)
    assert updated.indexed == 1 and updated.deleted_points == len(original_ids)
    assert not original_ids & await h.store.managed_ids()
    result = await h.service.search(SearchRequest(query="Java", mode="dense"))
    assert len(result.items) == 1
    async with async_sessionmaker(h.engine).begin() as session:
        await session.execute(delete(Job))
    assert (await h.service.search(SearchRequest(query="Java"))).items == []
    removed = await index_documents(h.engine, h.settings, h.embedder, h.store)
    assert removed.deleted_points > 0
    assert await h.store.managed_ids() == set()


async def test_crash_after_vector_write_resumes_without_duplicate_points(
    harness: Harness, record: JobRecord
) -> None:
    h = harness
    await import_records(h.engine, [record], h.settings)
    interrupted_id = uuid.uuid4()
    async with async_sessionmaker(h.engine).begin() as session:
        session.add(
            IndexingRun(
                id=interrupted_id,
                namespace=h.store.collection,
                model_signature=h.embedder.signature,
                status="running",
                result={},
            )
        )

    class CrashAfterWrite(QdrantStore):
        async def upsert(self, points):
            await super().upsert(points)
            raise VectorBackendUnavailable("synthetic crash after write")

    broken = CrashAfterWrite(h.store.client, h.store.collection, h.embedder.dimension)
    with pytest.raises(VectorBackendUnavailable):
        await index_documents(h.engine, h.settings, h.embedder, broken)
    ids = await h.store.managed_ids()
    assert ids
    async with async_sessionmaker(h.engine)() as session:
        assert await session.scalar(select(VectorIndexState.status)) == "pending"
        assert (await session.get(IndexingRun, interrupted_id)).status == "interrupted"
    assert (
        await h.service.search(SearchRequest(query="Python", mode="hybrid"))
    ).effective_mode == "bm25"
    resumed = await index_documents(h.engine, h.settings, h.embedder, h.store)
    assert resumed.indexed == 1
    assert await h.store.managed_ids() == ids
    await h.store.delete_ids([next(iter(ids))])
    repaired = await index_documents(h.engine, h.settings, h.embedder, h.store)
    assert repaired.indexed == 1
    assert await h.store.managed_ids() == ids


async def test_filters_apply_to_both_retrievers_before_fusion(
    harness: Harness, record: JobRecord
) -> None:
    h = harness
    other = record.model_copy(
        update={
            "source_job_id": "java-remote",
            "description": "Java Docker",
            "location": "Stockholm",
            "workplace_type": "remote",
        }
    )
    await import_records(h.engine, [record, other], h.settings)
    await index_documents(h.engine, h.settings, h.embedder, h.store)
    response = await h.service.search(
        SearchRequest(
            query="Java",
            mode="hybrid",
            filters=SearchFilters(location="Stockholm", workplace_type="remote", skills=["java"]),
        )
    )
    assert response.eligible_listings == 1
    assert len(response.items) == 1
    assert response.items[0].source_job_id == "java-remote"
    empty = await h.service.search(SearchRequest(query="Java", filters=SearchFilters(location="%")))
    assert empty.items == []  # LIKE wildcards in a literal location filter are escaped.


async def test_public_scope_excludes_private_rows_from_candidates_and_dedupe_counts(
    harness: Harness, record: JobRecord
) -> None:
    h = harness
    private = record.model_copy(
        update={
            "source": "itviec",
            "source_url": HttpUrl("https://itviec.com/it-jobs/synthetic-test-0001"),
        }
    )
    await import_records(h.engine, [record, private], h.settings)
    await index_documents(h.engine, h.settings, h.embedder, h.store)
    public = h.settings.model_copy(update={"app_env": "production"})
    store = QdrantStore(h.store.client, namespace(public, h.embedder), h.embedder.dimension)
    indexed = await index_documents(h.engine, public, h.embedder, store)
    assert indexed.indexed == 1
    service = SearchService(h.engine, public, h.embedder, store)
    for mode in ("bm25", "dense", "hybrid"):
        response = await service.search(SearchRequest(query="Python", mode=mode))
        assert response.eligible_listings == response.unique_documents == len(response.items) == 1
        assert response.items[0].source == "synthetic"
        assert response.items[0].duplicate_listings == 1


async def test_api_search_returns_components_and_rejects_unsupported_filters(
    harness: Harness, record: JobRecord
) -> None:
    h = harness
    await import_records(h.engine, [record], h.settings)
    await index_documents(h.engine, h.settings, h.embedder, h.store)

    def check() -> None:
        with TestClient(create_app(h.settings, embedder=h.embedder)) as client:
            response = client.post("/search", json={"query": "Python", "mode": "hybrid"})
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["effective_mode"] == "hybrid"
            assert body["items"][0]["bm25_score"] is not None
            assert body["items"][0]["dense_score"] is not None
            assert "raw_payload" not in response.text
            default = client.post("/search", json={"query": "Python"})
            assert default.json()["effective_mode"] == "hybrid"
            assert default.json()["items"][0]["dense_score"] is not None
            baseline = client.post("/search", json={"query": "Python", "mode": "bm25"})
            assert baseline.json()["effective_mode"] == "bm25"
            assert baseline.json()["items"][0]["dense_score"] is None
            assert (
                client.post(
                    "/search", json={"query": "Python", "filters": {"salary": 10}}
                ).status_code
                == 422
            )

    await asyncio.to_thread(check)


async def test_vector_outage_degrades_hybrid_but_not_explicit_dense(
    harness: Harness, record: JobRecord
) -> None:
    h = harness
    await import_records(h.engine, [record], h.settings)
    await index_documents(h.engine, h.settings, h.embedder, h.store)
    async with httpx.AsyncClient(
        base_url=h.settings.qdrant_url, transport=httpx.MockTransport(lambda _: httpx.Response(503))
    ) as client:
        store = QdrantStore(client, h.store.collection, h.embedder.dimension)
        service = SearchService(h.engine, h.settings, h.embedder, store)
        response = await service.search(SearchRequest(query="Python", mode="hybrid"))
        assert response.effective_mode == "bm25"
        assert len(response.items) == 1
        assert "VectorBackendUnavailable" in response.warnings
        with pytest.raises(DenseSearchUnavailable):
            await service.search(SearchRequest(query="Python", mode="dense"))


async def test_indexer_lock_prevents_concurrent_writers(
    harness: Harness, record: JobRecord
) -> None:
    h = harness
    await import_records(h.engine, [record], h.settings)
    entered, release = asyncio.Event(), asyncio.Event()

    class PausedStore(QdrantStore):
        async def upsert(self, points):
            entered.set()
            await release.wait()
            await super().upsert(points)

    paused = PausedStore(h.store.client, h.store.collection, h.embedder.dimension)
    task = asyncio.create_task(index_documents(h.engine, h.settings, h.embedder, paused))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        with pytest.raises(IndexAlreadyRunning):
            await index_documents(h.engine, h.settings, h.embedder, h.store)
    finally:
        release.set()
        await task


async def test_private_vectors_are_never_sent_to_remote_backend(
    harness: Harness, record: JobRecord
) -> None:
    h = harness
    private = record.model_copy(
        update={
            "source": "itviec",
            "source_url": HttpUrl("https://itviec.com/it-jobs/synthetic-test-0001"),
        }
    )
    await import_records(h.engine, [private], h.settings)

    def fail_request(_):
        pytest.fail("Private source identifiers/vectors must not reach a remote backend")

    async with httpx.AsyncClient(
        base_url="https://vectors.example.test", transport=httpx.MockTransport(fail_request)
    ) as client:
        store = QdrantStore(client, h.store.collection, h.embedder.dimension)
        with pytest.raises(ValueError, match="local Qdrant"):
            await index_documents(h.engine, h.settings, h.embedder, store)
        service = SearchService(h.engine, h.settings, h.embedder, store)
        result = await service.search(SearchRequest(query="Python", mode="hybrid"))
        assert result.effective_mode == "bm25"
        assert "local_source_requires_local_vector_backend" in result.warnings
        assert h.embedder.calls == 0


async def test_similar_jobs_rank_by_meaning_and_skip_the_job_and_its_duplicates(
    harness: Harness, record: JobRecord
) -> None:
    h = harness
    anchor = record.model_copy(update={"description": "Python Python Python services"})
    duplicate = anchor.model_copy(update={"source_job_id": "job-1-copy"})
    close = record.model_copy(
        update={"source_job_id": "job-2", "title": "Data Engineer", "description": "Python ETL"}
    )
    far = record.model_copy(
        update={
            "source_job_id": "job-3",
            "title": "Java Developer",
            "description": "Java Java Java Java Spring",
        }
    )
    await import_records(h.engine, [anchor, duplicate, close, far], h.settings)
    async with async_sessionmaker(h.engine)() as session:
        ids = dict((await session.execute(select(Job.source_job_id, Job.id))).tuples().all())
    with pytest.raises(DenseSearchUnavailable, match="dense_index_not_ready"):
        await h.service.similar(ids["job-1"], 5)
    await index_documents(h.engine, h.settings, h.embedder, h.store)

    # Either listing of the duplicated posting gives the same neighbours, never itself.
    for listing in ("job-1", "job-1-copy"):
        similar = await h.service.similar(ids[listing], 5)
        assert [item.title for item in similar] == ["Data Engineer", "Java Developer"]
        assert similar[0].score > similar[1].score
    assert [item.title for item in await h.service.similar(ids["job-1"], 1)] == ["Data Engineer"]
    with pytest.raises(SimilarJobNotFound):
        await h.service.similar(uuid.uuid4(), 5)

    def check() -> None:
        with TestClient(create_app(h.settings, embedder=h.embedder)) as client:
            response = client.get(f"/jobs/{ids['job-2']}/similar", params={"limit": 2})
            assert response.status_code == 200, response.text
            items = response.json()["items"]
            assert items[0]["title"] == "Backend Developer" and len(items) == 2
            assert set(items[0]) == {
                "job_id",
                "title",
                "company",
                "location",
                "workplace_type",
                "source",
                "score",
            }
            assert client.get(f"/jobs/{uuid.uuid4()}/similar").status_code == 404
            assert client.get(f"/jobs/{ids['job-2']}/similar?limit=11").status_code == 422

    await asyncio.to_thread(check)


async def test_similar_jobs_report_vector_outage(harness: Harness, record: JobRecord) -> None:
    h = harness
    await import_records(h.engine, [record], h.settings)
    await index_documents(h.engine, h.settings, h.embedder, h.store)
    async with async_sessionmaker(h.engine)() as session:
        job_id = await session.scalar(select(Job.id))
    assert job_id is not None
    async with httpx.AsyncClient(
        base_url=h.settings.qdrant_url, transport=httpx.MockTransport(lambda _: httpx.Response(503))
    ) as client:
        store = QdrantStore(client, h.store.collection, h.embedder.dimension)
        service = SearchService(h.engine, h.settings, h.embedder, store)
        with pytest.raises(VectorBackendUnavailable):
            await service.similar(job_id, 5)


async def test_similar_jobs_cap_each_company_and_skip_reposted_roles(
    harness: Harness, record: JobRecord
) -> None:
    h = harness
    rows = [record.model_copy(update={"description": "Python " * 6})]
    for number, (company, title) in enumerate(
        [
            ("Acme", "Python Developer"),
            ("Acme", "Python Developer"),  # same role re-posted with other text
            ("Acme", "Senior Python Developer"),
            ("Acme", "Python Lead"),
            ("Other", "Python Engineer"),
        ],
        start=2,
    ):
        rows.append(
            record.model_copy(
                update={
                    "source_job_id": f"job-{number}",
                    "company": company,
                    "title": title,
                    "description": "Python " * (6 + number) + f"posting {number}",
                }
            )
        )
    await import_records(h.engine, rows, h.settings)
    await index_documents(h.engine, h.settings, h.embedder, h.store)
    async with async_sessionmaker(h.engine)() as session:
        anchor = await session.scalar(select(Job.id).where(Job.source_job_id == "job-1"))
    assert anchor is not None
    similar = await h.service.similar(anchor, 10)
    companies = [item.company for item in similar]
    assert companies.count("Acme") == 2 and "Other" in companies
    roles = [(item.company, item.title) for item in similar]
    assert len(roles) == len(set(roles))
