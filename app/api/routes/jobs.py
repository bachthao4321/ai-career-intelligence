import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import JobServiceDep, ServicesDep, SettingsDep, paid_client_gate
from app.schemas.jobs import (
    DescriptionRequest,
    DescriptionResponse,
    JobDetail,
    JobPage,
    JobPresentation,
)
from app.services.jobs import JobNotFound, translate_job

router = APIRouter(prefix="/jobs", tags=["Jobs"])
routers = [router]


@router.get("", response_model=JobPage)
async def list_jobs(
    jobs: JobServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> JobPage:
    return await jobs.page(limit, offset)


@router.get("/presentation", response_model=dict[str, JobPresentation])
async def job_presentations(
    jobs: JobServiceDep,
    ids: Annotated[list[uuid.UUID], Query(min_length=1, max_length=20)],
) -> dict[str, JobPresentation]:
    return await jobs.presentations(ids)


@router.get("/{job_id}", response_model=JobDetail)
async def job_detail(job_id: uuid.UUID, jobs: JobServiceDep) -> JobDetail:
    try:
        return await jobs.detail(job_id)
    except JobNotFound as exc:
        raise HTTPException(404, "Job not found") from exc


@router.post("/{job_id}/description-translation", response_model=DescriptionResponse)
async def translate_description(
    request: Request,
    job_id: uuid.UUID,
    body: DescriptionRequest,
    jobs: JobServiceDep,
    services: ServicesDep,
    settings: SettingsDep,
) -> DescriptionResponse:
    try:
        job = await jobs.require(job_id)
    except JobNotFound as exc:
        raise HTTPException(404, "Job not found") from exc
    await paid_client_gate(request, settings.agent_model_enabled)
    try:
        return await translate_job(job, body.target, services.translator)
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
