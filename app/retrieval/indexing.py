import asyncio
import hashlib
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.db.models import IndexingRun, VectorIndexState
from app.ingestion.contracts import json_hash
from app.retrieval.documents import load_documents
from app.retrieval.embeddings import Embedder, validate_vectors
from app.retrieval.qdrant import MANAGED_BY, QdrantStore


def namespace(settings: Settings, embedder: Embedder) -> str:
    scope = "local" if settings.app_env == "local" else "public"
    return f"{settings.qdrant_collection_prefix}_{scope}_{embedder.signature[:16]}"


def version_key(job_id: uuid.UUID, index_hash: str, signature: str) -> str:
    return json_hash([str(job_id), index_hash, signature])


@dataclass
class IndexReport:
    indexed: int = 0
    skipped: int = 0
    deleted_points: int = 0
    truncated_jobs: int = 0
    points_written: int = 0


class IndexAlreadyRunning(RuntimeError):
    pass


async def index_documents(
    engine: AsyncEngine,
    settings: Settings,
    embedder: Embedder,
    store: QdrantStore,
    *,
    max_jobs: int | None = None,
) -> IndexReport:
    name = namespace(settings, embedder)
    if store.collection != name or store.dimension != embedder.dimension:
        raise ValueError("Index store does not match the model/scope namespace")
    if max_jobs is not None and max_jobs < 1:
        raise ValueError("max_jobs must be positive")
    lock_key = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)
    report = IndexReport()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    run_id = uuid.uuid4()
    async with engine.begin() as lock:
        acquired = await lock.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key}
        )
        if not acquired:
            raise IndexAlreadyRunning("Another indexer owns this model/scope namespace")
        try:
            async with sessions.begin() as session:
                await session.execute(
                    update(IndexingRun)
                    .where(
                        IndexingRun.namespace == name,
                        IndexingRun.status == "running",
                    )
                    .values(
                        status="interrupted",
                        completed_at=datetime.now(UTC),
                        result={"error": "previous_worker_interrupted"},
                    )
                )
                session.add(
                    IndexingRun(
                        id=run_id,
                        namespace=name,
                        model_signature=embedder.signature,
                        status="running",
                        result={},
                    )
                )
            documents = await load_documents(engine, settings)
            if any(doc.private for doc in documents) and not store.local:
                raise ValueError("Local-research records require a local Qdrant backend")
            await store.ensure_collection()
            desired_ids: set[str] = set()
            pending_count = 0
            for doc in documents:
                async with sessions() as session:
                    state = await session.get(VectorIndexState, (name, doc.job_id))
                if (
                    state is not None
                    and state.status == "ready"
                    and state.index_hash == doc.index_hash
                ):
                    present = await store.existing_ids(state.point_ids)
                    if present == set(state.point_ids) and present:
                        report.skipped += 1
                        report.truncated_jobs += int(state.truncated)
                        desired_ids.update(state.point_ids)
                        continue
                if max_jobs is not None and pending_count >= max_jobs:
                    continue
                pending_count += 1
                chunks, chunk_truncated = await asyncio.to_thread(embedder.chunks, doc.text)
                key = version_key(doc.job_id, doc.index_hash, embedder.signature)
                point_ids = [
                    str(uuid.uuid5(uuid.NAMESPACE_URL, f"{key}:{index}"))
                    for index in range(len(chunks))
                ]
                truncated = doc.truncated or chunk_truncated
                values = dict(
                    namespace=name,
                    job_id=doc.job_id,
                    index_hash=doc.index_hash,
                    version_key=key,
                    status="pending",
                    point_ids=point_ids,
                    truncated=truncated,
                    updated_at=datetime.now(UTC),
                )
                async with sessions.begin() as session:
                    await session.execute(
                        insert(VectorIndexState)
                        .values(**values)
                        .on_conflict_do_update(
                            index_elements=[VectorIndexState.namespace, VectorIndexState.job_id],
                            set_={
                                k: v for k, v in values.items() if k not in {"namespace", "job_id"}
                            },
                        )
                    )
                vectors = await asyncio.to_thread(embedder.encode, [chunk.text for chunk in chunks])
                validate_vectors(vectors, len(chunks), embedder.dimension)
                await store.upsert(
                    [
                        {
                            "id": point_id,
                            "vector": vector,
                            "payload": {
                                "managed_by": MANAGED_BY,
                                "job_id": str(doc.job_id),
                                "version_key": key,
                                "index_hash": doc.index_hash,
                                "chunk_index": index,
                            },
                        }
                        for index, (point_id, vector) in enumerate(
                            zip(point_ids, vectors, strict=True)
                        )
                    ]
                )
                async with sessions.begin() as session:
                    await session.execute(
                        update(VectorIndexState)
                        .where(
                            VectorIndexState.namespace == name,
                            VectorIndexState.job_id == doc.job_id,
                            VectorIndexState.version_key == key,
                        )
                        .values(status="ready", updated_at=datetime.now(UTC))
                    )
                desired_ids.update(point_ids)
                report.indexed += 1
                report.points_written += len(point_ids)
                report.truncated_jobs += int(truncated)
            # A bounded partial run is resumable and must not delete unprocessed live points.
            if max_jobs is None:
                stale = (await store.managed_ids()) - desired_ids
                await store.delete_ids(sorted(stale))
                report.deleted_points = len(stale)
                async with sessions.begin() as session:
                    statement = delete(VectorIndexState).where(VectorIndexState.namespace == name)
                    if documents:
                        statement = statement.where(
                            VectorIndexState.job_id.not_in([doc.job_id for doc in documents])
                        )
                    await session.execute(statement)
            async with sessions.begin() as session:
                await session.execute(
                    update(IndexingRun)
                    .where(IndexingRun.id == run_id)
                    .values(
                        status="partial" if max_jobs is not None else "completed",
                        result=asdict(report),
                        completed_at=datetime.now(UTC),
                    )
                )
            return report
        except Exception as exc:
            async with sessions.begin() as session:
                await session.execute(
                    update(IndexingRun)
                    .where(IndexingRun.id == run_id)
                    .values(
                        status="failed",
                        result={**asdict(report), "error": type(exc).__name__},
                        completed_at=datetime.now(UTC),
                    )
                )
            raise
