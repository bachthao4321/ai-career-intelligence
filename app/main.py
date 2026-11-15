from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import accounts, admin, ask, cv, health, jobs, platform, search, web
from app.core.config import Settings
from app.core.container import build_services
from app.core.middleware import install_security
from app.integrations.embeddings import Embedder


def create_app(settings: Settings | None = None, *, embedder: Embedder | None = None) -> FastAPI:
    config = Settings.model_validate(settings.model_dump()) if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with build_services(config, embedder) as services:
            app.state.services = services
            yield

    app = FastAPI(title=config.app_name, version="0.1.0", lifespan=lifespan)
    app.state.settings = config
    for module in (health, jobs, search, ask, admin, cv, platform, accounts, web):
        for router in module.routers:
            app.include_router(router)
    app.mount("/assets", StaticFiles(directory=web.STATIC), name="platform-assets")
    install_security(app, config)
    return app
