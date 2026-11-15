import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import ServicesDep, SettingsDep, require_admin
from app.core.policies import local_research_only
from app.ingestion.service import import_records
from app.integrations.embeddings import EmbeddingUnavailable
from app.integrations.qdrant import VectorBackendUnavailable
from app.schemas.admin import ImportInput, IndexInput
from app.services.search.indexing import IndexAlreadyRunning, index_documents

router = APIRouter(prefix="/admin", tags=["Operator"], dependencies=[Depends(require_admin)])
routers = [router]


@router.post("/import")
async def import_jobs(
    body: ImportInput, services: ServicesDep, settings: SettingsDep
) -> dict[str, int]:
    if settings.app_env != "local" and any(
        local_research_only(r.source, str(r.source_url)) for r in body.records
    ):
        raise HTTPException(422, "Restricted research sources cannot be imported in public scope")
    try:
        report = await import_records(services.engine, body.records, settings)
        return report.as_dict()
    except SQLAlchemyError as exc:
        raise HTTPException(503, "Import storage unavailable") from exc


@router.post("/index")
async def index_jobs(body: IndexInput, services: ServicesDep) -> dict[str, int]:
    search = services.search
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
