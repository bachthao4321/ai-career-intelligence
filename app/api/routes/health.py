import asyncio
import logging
from typing import Annotated, Literal, Protocol, cast

import httpx
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Operations"])


class Probes(Protocol):
    async def postgres(self) -> None: ...

    async def qdrant(self) -> None: ...


class DependencyProbes:
    def __init__(self, engine: AsyncEngine, client: httpx.AsyncClient) -> None:
        self.engine = engine
        self.client = client

    async def postgres(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def qdrant(self) -> None:
        response = await self.client.get("/readyz")
        response.raise_for_status()


def get_probes(request: Request) -> Probes:
    return cast(Probes, request.app.state.probes)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    dependencies: dict[str, Literal["ok", "unavailable"]]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/ready", response_model=ReadyResponse, responses={503: {"model": ReadyResponse}})
async def ready(
    request: Request, response: Response, probes: Annotated[Probes, Depends(get_probes)]
) -> ReadyResponse:
    async def check(name: str) -> Literal["ok", "unavailable"]:
        try:
            async with asyncio.timeout(request.app.state.dependency_timeout_seconds):
                if name == "postgres":
                    await probes.postgres()
                else:
                    await probes.qdrant()
            return "ok"
        except Exception:
            # Never expose connection strings, provider responses, or credentials.
            logger.warning("Readiness dependency unavailable: %s", name)
            return "unavailable"

    postgres, qdrant = await asyncio.gather(check("postgres"), check("qdrant"))
    dependencies = {"postgres": postgres, "qdrant": qdrant}
    is_ready = all(value == "ok" for value in dependencies.values())
    response.headers["Cache-Control"] = "no-store"
    if not is_ready:
        response.status_code = 503
    return ReadyResponse(status="ready" if is_ready else "not_ready", dependencies=dependencies)
