from typing import cast

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from app.core.security.demo import paid_client_gate
from app.services.ask.workflow import AskRequest, AskResponse, CareerAgent
from app.services.search.documents import CorpusTooLarge
from app.services.search.service import DenseSearchUnavailable

router = APIRouter(tags=["Career agent"])


@router.post("/ask", response_model=AskResponse)
async def ask(body: AskRequest, request: Request) -> AskResponse:
    agent = cast(CareerAgent, request.app.state.career_agent)
    config = request.app.state.settings
    await paid_client_gate(
        request, config.agent_model_enabled and config.agent_daily_call_limit > 0
    )
    try:
        return await agent.ask(body)
    except CorpusTooLarge as exc:
        raise HTTPException(422, "Search corpus exceeds configured limit") from exc
    except (SQLAlchemyError, DenseSearchUnavailable) as exc:
        raise HTTPException(503, "Search or model budget storage unavailable") from exc
