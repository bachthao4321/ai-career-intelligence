import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app.agents.analytics import AnalyticsExecutor
from app.agents.planner import ModelPlanner
from app.agents.workflow import CareerAgent
from app.api.admin import router as admin_router
from app.api.ask import router as ask_router
from app.api.auth import router as auth_router
from app.api.auth import saved_router
from app.api.cv import router as cv_router
from app.api.health import DependencyProbes, router
from app.api.jobs import router as jobs_router
from app.api.platform import router as platform_router
from app.api.search import router as search_router
from app.core.config import Settings
from app.cv.service import CVService
from app.db.session import create_engine
from app.ingestion.description_translation import DescriptionTranslator
from app.llm.runtime import ModelRuntime
from app.retrieval.embeddings import Embedder, LocalMiniLM
from app.retrieval.indexing import namespace
from app.retrieval.qdrant import QdrantStore
from app.retrieval.reranking import Reranker
from app.retrieval.service import SearchService
from app.security.demo import (
    client_hash,
    cookie_name,
    host_allowed,
    issue_session,
    reserve_client,
    same_origin,
    valid_basic,
    valid_session,
)
from app.web.routes import STATIC
from app.web.routes import router as web_router


def create_app(settings: Settings | None = None, *, embedder: Embedder | None = None) -> FastAPI:
    config = Settings.model_validate(settings.model_dump()) if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(config)
        analytics = AnalyticsExecutor(config)
        cv_service = CVService(engine, config)
        app.state.cv_service = cv_service
        cleanup_task = asyncio.create_task(cv_service.cleanup_loop())
        app.state.engine = engine
        app.state.description_translator = DescriptionTranslator(config, engine)
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
                    app.state.search_service = SearchService(
                        engine,
                        config,
                        encoder,
                        store,
                        reranker=Reranker(ModelRuntime(engine, config), config),
                    )
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
    application.state.settings = config
    application.include_router(router)
    application.include_router(jobs_router)
    application.include_router(search_router)
    application.include_router(ask_router)
    application.include_router(admin_router)
    application.include_router(cv_router)
    application.include_router(platform_router)
    application.include_router(auth_router)
    application.include_router(saved_router)
    application.include_router(web_router)
    application.mount("/assets", StaticFiles(directory=STATIC), name="platform-assets")

    @application.middleware("http")
    async def private_cv_responses(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        def rejected(code: int, detail: str, headers: dict[str, str] | None = None) -> Response:
            return JSONResponse(
                {"detail": detail},
                status_code=code,
                headers={"Cache-Control": "no-store", **(headers or {})},
            )

        if not host_allowed(request, config):
            return rejected(400, "Unrecognized host")
        probe = request.url.path in {"/health", "/ready"} and request.method in {"GET", "HEAD"}
        if not probe:
            if config.client_limits_enabled:
                try:
                    granted = await reserve_client(
                        engine=request.app.state.engine,
                        identity=client_hash(request, config),
                        category="minute",
                        limit=config.client_requests_per_minute,
                    )
                except SQLAlchemyError:
                    return rejected(503, "Client quota storage unavailable")
                if not granted:
                    return rejected(429, "Request rate limit reached", {"Retry-After": "60"})
            login = False
            if config.demo_auth_enabled:
                authenticated = valid_session(config, request.cookies.get(cookie_name(config), ""))
                login = valid_basic(config, request.headers.get("authorization", ""))
                if not authenticated and not login:
                    return rejected(
                        401,
                        "Demo login required",
                        {"WWW-Authenticate": 'Basic realm="CareerLens", charset="UTF-8"'},
                    )
                if request.method not in {"GET", "HEAD", "OPTIONS"} and not same_origin(
                    request, config
                ):
                    return rejected(403, "Same-origin request required")
            response = await call_next(request)
            if config.demo_auth_enabled and login:
                response.set_cookie(
                    cookie_name(config),
                    issue_session(config),
                    max_age=config.demo_session_seconds,
                    httponly=True,
                    secure=config.app_env == "production",
                    samesite="strict",
                    path="/",
                )
            if config.demo_auth_enabled:
                response.headers["Cache-Control"] = "no-store"
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith(("/cv", "/auth/", "/me/")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    return application
