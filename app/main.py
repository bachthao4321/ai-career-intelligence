import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, Request, Response

from app.agents.analytics import AnalyticsExecutor
from app.agents.planner import ModelPlanner
from app.agents.workflow import CareerAgent
from app.api.ask import router as ask_router
from app.api.cv import router as cv_router
from app.api.health import DependencyProbes, router
from app.api.jobs import router as jobs_router
from app.api.search import router as search_router
from app.core.config import Settings
from app.cv.service import CVService
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
        cv_service = CVService(engine, config)
        app.state.cv_service = cv_service
        cleanup_task = asyncio.create_task(cv_service.cleanup_loop())
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
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
            await analytics.close()
            await engine.dispose()

    application = FastAPI(title=config.app_name, version="0.1.0", lifespan=lifespan)
    application.state.dependency_timeout_seconds = config.dependency_timeout_seconds
    application.state.app_env = config.app_env
    application.include_router(router)
    application.include_router(jobs_router)
    application.include_router(search_router)
    application.include_router(ask_router)
    application.include_router(cv_router)

    @application.middleware("http")
    async def private_cv_responses(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        if request.url.path == "/cv" or request.url.path.startswith("/cv/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    return application
