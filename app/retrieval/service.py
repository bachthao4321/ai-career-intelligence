import asyncio
import math
import re
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.db.models import VectorIndexState
from app.retrieval.contracts import RankedHit, SearchHit, SearchRequest, SearchResponse
from app.retrieval.documents import deduplicate, load_documents
from app.retrieval.embeddings import Embedder, EmbeddingUnavailable, validate_vectors
from app.retrieval.indexing import namespace, version_key
from app.retrieval.lexical import bm25, reciprocal_rank_fusion, tokenize
from app.retrieval.qdrant import QdrantStore, VectorBackendUnavailable


class DenseSearchUnavailable(RuntimeError):
    pass


class SearchService:
    def __init__(
        self, engine: AsyncEngine, settings: Settings, embedder: Embedder, store: QdrantStore
    ):
        self.engine, self.settings, self.embedder, self.store = engine, settings, embedder, store
        if (
            store.collection != namespace(settings, embedder)
            or store.dimension != embedder.dimension
        ):
            raise ValueError("Search store does not match the model/scope namespace")

    async def search(
        self,
        request: SearchRequest,
        *,
        rrf_k: int | None = None,
        rrf_weights: tuple[float, float] | None = None,
    ) -> SearchResponse:
        rrf_k = rrf_k if rrf_k is not None else self.settings.rrf_k
        rrf_weights = rrf_weights or (self.settings.rrf_bm25_weight, self.settings.rrf_dense_weight)
        started = time.perf_counter()
        all_docs = await load_documents(self.engine, self.settings, request.filters)
        documents, counts = deduplicate(all_docs)
        by_id = {doc.job_id: doc for doc in documents}
        warnings: list[str] = []
        lexical: list[RankedHit] = []
        dense: list[RankedHit] = []
        indexed: int | None = None if request.mode == "bm25" else 0
        if any(doc.truncated for doc in documents):
            warnings.append("search_document_limited_to_20000_characters")
        if request.mode != "dense" and tokenize(request.query):
            lexical = await asyncio.to_thread(
                bm25, request.query, documents, limit=request.candidate_k
            )
        if request.mode != "bm25" and documents and tokenize(request.query):
            try:
                if any(doc.private for doc in documents) and not self.store.local:
                    raise DenseSearchUnavailable("local_source_requires_local_vector_backend")
                async with async_sessionmaker(self.engine)() as session:
                    states = (
                        await session.scalars(
                            select(VectorIndexState).where(
                                VectorIndexState.namespace
                                == namespace(self.settings, self.embedder),
                                VectorIndexState.job_id.in_(by_id),
                                VectorIndexState.status == "ready",
                            )
                        )
                    ).all()
                current = {
                    state.job_id: state
                    for state in states
                    if state.index_hash == by_id[state.job_id].index_hash
                    and state.version_key
                    == version_key(state.job_id, state.index_hash, self.embedder.signature)
                }
                indexed = len(current)
                if not current:
                    raise DenseSearchUnavailable("dense_index_not_ready")
                if indexed < len(documents):
                    warnings.append("dense_index_incomplete_run_index")
                if any(state.truncated for state in current.values()):
                    warnings.append("dense_chunk_limit_reached_for_some_documents")
                vectors = await asyncio.to_thread(self.embedder.encode, [request.query])
                validate_vectors(vectors, 1, self.embedder.dimension)
                rows = await self.store.query(
                    vectors[0],
                    [state.version_key for state in current.values()],
                    request.candidate_k,
                )
                for row in rows:
                    try:
                        payload = row["payload"]
                        job_id = uuid.UUID(payload["job_id"])
                        state = current[job_id]
                        score = float(row["score"])
                        chunk = payload["chunk_index"]
                        if (
                            payload["version_key"] != state.version_key
                            or payload["index_hash"] != state.index_hash
                            or type(chunk) is not int
                            or not 0 <= chunk < len(state.point_ids)
                            or str(row["id"]) != state.point_ids[chunk]
                            or not math.isfinite(score)
                            or not -1.001 <= score <= 1.001
                        ):
                            raise ValueError("Stale or invalid vector hit")
                        dense.append(RankedHit(job_id, score, chunk))
                    except (KeyError, TypeError, ValueError):
                        warnings.append("invalid_vector_result_ignored")
                dense.sort(key=lambda hit: (-hit.score, str(hit.job_id)))
                if not dense:
                    raise DenseSearchUnavailable("vector_points_missing_run_index")
            except (
                EmbeddingUnavailable,
                VectorBackendUnavailable,
                DenseSearchUnavailable,
                ValueError,
            ) as exc:
                indexed = 0
                reason = str(exc) if isinstance(exc, DenseSearchUnavailable) else type(exc).__name__
                if request.mode == "dense":
                    raise DenseSearchUnavailable(reason) from exc
                warnings.append(reason)
        effective = request.mode
        if request.mode == "hybrid":
            effective = "hybrid" if dense else "bm25"
            ranking = (
                reciprocal_rank_fusion([lexical, dense], k=rrf_k, weights=list(rrf_weights))
                if dense
                else lexical
            )
        else:
            ranking = dense if request.mode == "dense" else lexical
        lexical_scores = {hit.job_id: hit.score for hit in lexical}
        dense_hits = {hit.job_id: hit for hit in dense}
        items = []
        for hit in ranking[: request.top_k]:
            doc = by_id[hit.job_id]
            start, end = 0, min(300, len(doc.text))
            if hit.job_id in dense_hits:
                chunks, _ = await asyncio.to_thread(self.embedder.chunks, doc.text)
                chunk_index = dense_hits[hit.job_id].chunk_index
                if chunk_index is not None and chunk_index < len(chunks):
                    start, end = (
                        chunks[chunk_index].start,
                        min(chunks[chunk_index].end, chunks[chunk_index].start + 300),
                    )
            else:
                for term in tokenize(request.query):
                    match = re.search(re.escape(term), doc.text, re.IGNORECASE)
                    if match:
                        start = max(0, match.start() - 70)
                        end = min(len(doc.text), start + 300)
                        break
            items.append(
                SearchHit(
                    job_id=doc.job_id,
                    title=doc.title,
                    company=doc.company,
                    location=doc.location,
                    workplace_type=doc.workplace_type,
                    source=doc.source,
                    source_job_id=doc.source_job_id,
                    source_url=doc.source_url,
                    usage_scope="local_research_only" if doc.private else "source_terms_apply",
                    content_hash=doc.content_hash,
                    duplicate_listings=counts[doc.content_hash],
                    score=hit.score,
                    bm25_score=lexical_scores.get(doc.job_id),
                    dense_score=dense_hits[doc.job_id].score if doc.job_id in dense_hits else None,
                    excerpt=doc.text[start:end],
                )
            )
        return SearchResponse(
            requested_mode=request.mode,
            effective_mode=effective,
            items=items,
            applied_filters=request.filters,
            eligible_listings=len(all_docs),
            unique_documents=len(documents),
            indexed_documents=indexed,
            warnings=list(dict.fromkeys(warnings)),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            model_signature=self.embedder.signature if request.mode != "bm25" else None,
            rrf_k=rrf_k,
            rrf_weights=rrf_weights,
        )
