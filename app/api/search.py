from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from app.retrieval.contracts import SearchRequest, SearchResponse
from app.retrieval.documents import CorpusTooLarge
from app.retrieval.service import DenseSearchUnavailable, SearchService

router = APIRouter(tags=["Search"])


def get_search_service(request: Request) -> SearchService:
    return cast(SearchService, request.app.state.search_service)


@router.post("/search", response_model=SearchResponse)
async def search_jobs(
    body: SearchRequest, service: Annotated[SearchService, Depends(get_search_service)]
) -> SearchResponse:
    try:
        return await service.search(body)
    except CorpusTooLarge as exc:
        raise HTTPException(
            status_code=422, detail="Corpus limit exceeded; narrow search filters"
        ) from exc
    except DenseSearchUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="Dense index/model unavailable; run model download and indexing"
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Search storage unavailable") from exc
