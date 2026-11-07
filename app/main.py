from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.agents.analytics import AnalyticsExecutor
from app.agents.planner import ModelPlanner
from app.agents.workflow import CareerAgent
from app.api.ask import router as ask_router
from app.api.health import DependencyProbes, router
from app.api.jobs import router as jobs_router
from app.api.search import router as search_router
from app.core.config import Settings
from app.db.session import create_engine
from app.retrieval.embeddings import Embedder, LocalMiniLM
from app.retrieval.indexing import namespace
from app.retrieval.qdrant import QdrantStore
from app.retrieval.service import SearchService


def create_app(settings: Settings | None = None, *, embedder: Embedder | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(config)
        analytics = AnalyticsExecutor(config)
        app.state.engine = engine
        try:
            async with httpx.AsyncClient(
                base_url=config.qdrant_url,
                timeout=config.dependency_timeout_seconds,
                trust_env=False,
            ) as client:
                app.state.probes = DependencyProbes(engine, client)
                encoder = embedder or LocalMiniLM(
                    config.embedding_model_dir, threads=config.embedding_threads
                )
                async with httpx.AsyncClient(
                    base_url=config.qdrant_url,
                    timeout=config.vector_timeout_seconds,
                    trust_env=False,
                ) as vector_client:
                    store = QdrantStore(
                        vector_client, namespace(config, encoder), encoder.dimension
                    )
                    app.state.search_service = SearchService(engine, config, encoder, store)
                    app.state.career_agent = CareerAgent(
                        app.state.search_service, analytics, ModelPlanner(config, engine)
                    )
                    yield
        finally:
            await analytics.close()
            await engine.dispose()

    application = FastAPI(title=config.app_name, version="0.1.0", lifespan=lifespan)
    application.state.dependency_timeout_seconds = config.dependency_timeout_seconds
    application.state.app_env = config.app_env
    application.include_router(router)
    application.include_router(jobs_router)
    application.include_router(search_router)
    application.include_router(ask_router)
    return application
