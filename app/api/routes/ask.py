from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import ServicesDep, SettingsDep, paid_client_gate
from app.schemas.ask import AskRequest, AskResponse
from app.services.search.documents import CorpusTooLarge
from app.services.search.service import DenseSearchUnavailable

router = APIRouter(tags=["Career agent"])
routers = [router]


@router.post("/ask", response_model=AskResponse)
async def ask(
    body: AskRequest, request: Request, services: ServicesDep, settings: SettingsDep
) -> AskResponse:
    await paid_client_gate(
        request, settings.agent_model_enabled and settings.agent_daily_call_limit > 0
    )
    try:
        return await services.agent.ask(body)
    except CorpusTooLarge as exc:
        raise HTTPException(422, "Search corpus exceeds configured limit") from exc
    except (SQLAlchemyError, DenseSearchUnavailable) as exc:
        raise HTTPException(503, "Search or model budget storage unavailable") from exc
