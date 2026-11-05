from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.health import DependencyProbes, router
from app.api.jobs import router as jobs_router
from app.core.config import Settings
from app.db.session import create_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(config)
        app.state.engine = engine
        try:
            async with httpx.AsyncClient(
                base_url=config.qdrant_url,
                timeout=config.dependency_timeout_seconds,
                trust_env=False,
            ) as client:
                app.state.probes = DependencyProbes(engine, client)
                yield
        finally:
            await engine.dispose()

    application = FastAPI(title=config.app_name, version="0.1.0", lifespan=lifespan)
    application.state.dependency_timeout_seconds = config.dependency_timeout_seconds
    application.state.app_env = config.app_env
    application.include_router(router)
    application.include_router(jobs_router)
    return application
