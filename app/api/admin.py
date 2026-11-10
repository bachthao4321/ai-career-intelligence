import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.ingestion.contracts import JobRecord
from app.ingestion.policies import local_research_only
from app.ingestion.service import import_records
from app.retrieval.embeddings import EmbeddingUnavailable
from app.retrieval.indexing import IndexAlreadyRunning, index_documents
from app.retrieval.qdrant import VectorBackendUnavailable
from app.security.demo import require_admin

router = APIRouter(prefix="/admin", tags=["Operator"], dependencies=[Depends(require_admin)])


class ImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    records: list[JobRecord] = Field(min_length=1, max_length=20)


class IndexInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_jobs: int = Field(default=5, ge=1, le=5)


@router.post("/import")
async def import_jobs(body: ImportInput, request: Request) -> dict[str, int]:
    settings = request.app.state.settings
    if settings.app_env != "local" and any(
        local_research_only(r.source, str(r.source_url)) for r in body.records
    ):
        raise HTTPException(422, "Restricted research sources cannot be imported in public scope")
    try:
        report = await import_records(request.app.state.engine, body.records, settings)
        return report.as_dict()
    except SQLAlchemyError as exc:
        raise HTTPException(503, "Import storage unavailable") from exc


@router.post("/index")
async def index_jobs(body: IndexInput, request: Request) -> dict[str, int]:
    search = request.app.state.search_service
    try:
        async with asyncio.timeout(60):
            report = await index_documents(
                search.engine,
                search.settings,
                search.embedder,
                search.store,
                max_jobs=body.max_jobs,
            )
        return asdict(report)
    except IndexAlreadyRunning as exc:
        raise HTTPException(409, "Indexing already in progress") from exc
    except (SQLAlchemyError, EmbeddingUnavailable, VectorBackendUnavailable, TimeoutError) as exc:
        raise HTTPException(503, "Index unavailable or interrupted; retry to resume") from exc
