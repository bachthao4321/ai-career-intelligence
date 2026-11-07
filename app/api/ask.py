from typing import cast

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from app.agents.workflow import AskRequest, AskResponse, CareerAgent
from app.retrieval.documents import CorpusTooLarge
from app.retrieval.service import DenseSearchUnavailable

router = APIRouter(tags=["Career agent"])


@router.post("/ask", response_model=AskResponse)
async def ask(body: AskRequest, request: Request) -> AskResponse:
    agent = cast(CareerAgent, request.app.state.career_agent)
    try:
        return await agent.ask(body)
    except CorpusTooLarge as exc:
        raise HTTPException(422, "Search corpus exceeds configured limit") from exc
    except (SQLAlchemyError, DenseSearchUnavailable) as exc:
        raise HTTPException(503, "Search or model budget storage unavailable") from exc
