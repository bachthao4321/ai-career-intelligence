import asyncio
import math
import re
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.db.models import VectorIndexState
from app.integrations.embeddings import Embedder, EmbeddingUnavailable, validate_vectors
from app.integrations.llm.runtime import CallBudget
from app.integrations.qdrant import QdrantStore, VectorBackendUnavailable
from app.schemas.search import (
    RankedHit,
    RerankInfo,
    SearchDocument,
    SearchHit,
    SearchRequest,
    SearchResponse,
    SimilarJob,
)
from app.services.search.documents import deduplicate, load_documents
from app.services.search.indexing import namespace, version_key
from app.services.search.lexical import bm25, reciprocal_rank_fusion, tokenize
from app.services.search.reranking import Reranker


class DenseSearchUnavailable(RuntimeError):
    pass


class SimilarJobNotFound(LookupError):
    pass


MAX_PER_COMPANY = 2
PER_COMPANY_FETCH = 6
ROLE_WEIGHT = 1.0  # title + skills vector weight relative to the whole-posting mean
SKILL_WEIGHT = 0.15  # bonus for extracted-skill overlap (Jaccard) when ranking neighbours


def unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(norm) or norm == 0:
        raise DenseSearchUnavailable("vector_points_missing_run_index")
    return [value / norm for value in vector]


class SearchService:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: Settings,
        embedder: Embedder,
        store: QdrantStore,
        *,
        reranker: Reranker | None = None,
    ):
        self.engine, self.settings, self.embedder, self.store = engine, settings, embedder, store
        self.reranker = reranker
        if (
            store.collection != namespace(settings, embedder)
            or store.dimension != embedder.dimension
        ):
            raise ValueError("Search store does not match the model/scope namespace")

    async def _current_states(
        self, by_id: dict[uuid.UUID, SearchDocument]
    ) -> dict[uuid.UUID, VectorIndexState]:
        """Ready index states whose vectors match the current document text and model."""
        async with async_sessionmaker(self.engine)() as session:
            states = (
                await session.scalars(
                    select(VectorIndexState).where(
                        VectorIndexState.namespace == namespace(self.settings, self.embedder),
                        VectorIndexState.job_id.in_(by_id),
                        VectorIndexState.status == "ready",
                    )
                )
            ).all()
        return {
            state.job_id: state
            for state in states
            if state.index_hash == by_id[state.job_id].index_hash
            and state.version_key
            == version_key(state.job_id, state.index_hash, self.embedder.signature)
        }

    @staticmethod
    def _dense_hits(
        rows: list[dict[str, Any]],
        current: dict[uuid.UUID, VectorIndexState],
        warnings: list[str],
    ) -> list[RankedHit]:
        """Keep only hits that point at the current version of an indexed document."""
        hits = []
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
                hits.append(RankedHit(job_id, score, chunk))
            except (KeyError, TypeError, ValueError):
                warnings.append("invalid_vector_result_ignored")
        return hits

    async def similar(self, job_id: uuid.UUID, limit: int) -> list[SimilarJob]:
        """Nearest visible jobs to one job, from the mean of its stored chunk vectors.

        No query is embedded, so this works without the model files. Duplicate listings of
        the same posting are collapsed first, so the job never recommends itself.
        """
        all_docs = await load_documents(self.engine, self.settings)
        target = next((doc for doc in all_docs if doc.job_id == job_id), None)
        if target is None:
            raise SimilarJobNotFound(str(job_id))
        documents, _ = deduplicate(all_docs)
        by_id = {doc.job_id: doc for doc in documents}
        if any(doc.private for doc in documents) and not self.store.local:
            raise DenseSearchUnavailable("local_source_requires_local_vector_backend")
        anchor = next(doc for doc in documents if doc.content_hash == target.content_hash)
        current = await self._current_states(by_id)
        state = current.get(anchor.job_id)
        if state is None:
            raise DenseSearchUnavailable("dense_index_not_ready")
        vectors = await self.store.vectors(list(state.point_ids))
        if not vectors:
            raise DenseSearchUnavailable("vector_points_missing_run_index")
        mean = unit([sum(column) / len(vectors) for column in zip(*vectors, strict=True)])
        # The body is mostly employer boilerplate; the role is in the title and skills.
        role = f"{anchor.title}\n{', '.join(anchor.skills)}"
        try:
            encoded = await asyncio.to_thread(self.embedder.encode, [role])
            validate_vectors(encoded, 1, self.embedder.dimension)
            query = unit([a + ROLE_WEIGHT * b for a, b in zip(mean, unit(encoded[0]), strict=True)])
        except (EmbeddingUnavailable, ValueError):
            query = mean  # Model files missing: stored vectors alone still work.
        others = [s.version_key for key, s in current.items() if key != anchor.job_id]
        # Postings from one employer share boilerplate (company intro, benefits), so they
        # crowd the nearest neighbours; over-fetch and cap each company to keep variety.
        rows = await self.store.query(query, others, min(100, limit * PER_COMPANY_FETCH))
        anchor_skills = set(anchor.skills)

        def blended(hit: RankedHit) -> float:
            skills = set(by_id[hit.job_id].skills)
            union = anchor_skills | skills
            overlap = len(anchor_skills & skills) / len(union) if union else 0.0
            return hit.score + SKILL_WEIGHT * overlap

        hits = self._dense_hits(rows, current, [])
        hits.sort(key=lambda hit: (-blended(hit), str(hit.job_id)))
        per_company: dict[str, int] = {}
        # Re-posts of one role in several cities have different text but the same name.
        seen_roles = {(anchor.company or "").casefold().strip() + "|" + anchor.title.casefold()}
        result = []
        for hit in hits:
            doc = by_id[hit.job_id]
            company = (doc.company or "").casefold().strip() or str(doc.job_id)
            role = company + "|" + doc.title.casefold().strip()
            if (
                hit.job_id == anchor.job_id
                or role in seen_roles
                or per_company.get(company, 0) >= MAX_PER_COMPANY
            ):
                continue
            seen_roles.add(role)
            per_company[company] = per_company.get(company, 0) + 1
            result.append(
                SimilarJob(
                    job_id=doc.job_id,
                    title=doc.title,
                    company=doc.company,
                    location=doc.location,
                    workplace_type=doc.workplace_type,
                    source=doc.source,
                    score=round(blended(hit), 4),
                )
            )
            if len(result) == limit:
                break
        return result

    async def search(
        self,
        request: SearchRequest,
        *,
        rrf_k: int | None = None,
        rrf_weights: tuple[float, float] | None = None,
        call_budget: CallBudget | None = None,
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
                current = await self._current_states(by_id)
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
                dense = self._dense_hits(rows, current, warnings)
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
        rerank_info = RerankInfo()
        rerank_scores: dict[str, float] = {}
        if request.rerank:
            if self.reranker is None:
                rerank_info = RerankInfo(status="disabled", reason="reranker_not_configured")
            else:
                reranked = await self.reranker.rerank(
                    request.query,
                    ranking,
                    documents,
                    request.filters.model_dump(mode="json"),
                    budget=call_budget,
                )
                ranking, rerank_info, rerank_scores = reranked.hits, reranked.info, reranked.scores
            if rerank_info.status != "applied":
                warnings.append("rerank_fallback_" + (rerank_info.reason or "unavailable"))
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
                    rerank_score=rerank_scores.get(str(doc.job_id)),
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
            rerank=rerank_info,
        )
