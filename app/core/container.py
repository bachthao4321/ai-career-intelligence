"""Builds the long-lived services once per process and tears them down on shutdown."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.session import create_engine
from app.integrations.embeddings import Embedder, LocalMiniLM
from app.integrations.llm.runtime import ModelRuntime
from app.integrations.probes import DependencyProbes
from app.integrations.qdrant import QdrantStore
from app.services.ask.analytics import AnalyticsExecutor
from app.services.ask.planner import ModelPlanner
from app.services.ask.workflow import CareerAgent
from app.services.cv.service import CVService
from app.services.search.indexing import namespace
from app.services.search.reranking import Reranker
from app.services.search.service import SearchService
from app.services.translation.descriptions import DescriptionTranslator


@dataclass(frozen=True)
class Services:
    engine: AsyncEngine
    probes: DependencyProbes
    search: SearchService
    agent: CareerAgent
    cv: CVService
    translator: DescriptionTranslator


def qdrant_client(settings: Settings, timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=settings.qdrant_url, timeout=timeout, trust_env=False)


@asynccontextmanager
async def build_services(
    settings: Settings, embedder: Embedder | None = None
) -> AsyncIterator[Services]:
    engine = create_engine(settings)
    analytics = AnalyticsExecutor(settings)
    cv = CVService(engine, settings)
    cleanup = asyncio.create_task(cv.cleanup_loop())
    try:
        async with AsyncExitStack() as stack:
            probe_client = await stack.enter_async_context(
                qdrant_client(settings, settings.dependency_timeout_seconds)
            )
            vector_client = await stack.enter_async_context(
                qdrant_client(settings, settings.vector_timeout_seconds)
            )
            encoder = embedder or LocalMiniLM(
                settings.embedding_model_dir, threads=settings.embedding_threads
            )
            store = QdrantStore(vector_client, namespace(settings, encoder), encoder.dimension)
            search = SearchService(
                engine,
                settings,
                encoder,
                store,
                reranker=Reranker(ModelRuntime(engine, settings), settings),
            )
            yield Services(
                engine=engine,
                probes=DependencyProbes(engine, probe_client),
                search=search,
                agent=CareerAgent(search, analytics, ModelPlanner(settings, engine)),
                cv=cv,
                translator=DescriptionTranslator(settings, engine),
            )
    finally:
        cleanup.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup
        await analytics.close()
        await engine.dispose()
