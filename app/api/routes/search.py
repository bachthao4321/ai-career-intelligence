from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import ServicesDep, paid_client_gate
from app.schemas.search import SearchRequest, SearchResponse
from app.services.search.documents import CorpusTooLarge
from app.services.search.service import DenseSearchUnavailable

router = APIRouter(tags=["Search"])
routers = [router]


@router.post("/search", response_model=SearchResponse)
async def search_jobs(
    body: SearchRequest, request: Request, services: ServicesDep
) -> SearchResponse:
    settings = services.search.settings
    await paid_client_gate(
        request, body.rerank and settings.rerank_enabled and settings.llm_daily_call_limit > 0
    )
    try:
        return await services.search.search(body)
    except CorpusTooLarge as exc:
        raise HTTPException(422, "Corpus limit exceeded; narrow search filters") from exc
    except DenseSearchUnavailable as exc:
        raise HTTPException(
            503, "Dense index/model unavailable; run model download and indexing"
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(503, "Search storage unavailable") from exc
