import asyncio
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import SettingsDep, get_services
from app.integrations.probes import Probes
from app.schemas.platform import HealthResponse, ReadyResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Operations"])
routers = [router]


def get_probes(request: Request) -> Probes:
    return get_services(request).probes


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/ready", response_model=ReadyResponse, responses={503: {"model": ReadyResponse}})
async def ready(
    response: Response, settings: SettingsDep, probes: Annotated[Probes, Depends(get_probes)]
) -> ReadyResponse:
    async def check(name: str) -> Literal["ok", "unavailable"]:
        try:
            async with asyncio.timeout(settings.dependency_timeout_seconds):
                await (probes.postgres() if name == "postgres" else probes.qdrant())
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
