import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Literal, Self, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import Job, JobSkill
from app.db.visibility import visible_jobs
from app.ingestion.policies import local_research_only

router = APIRouter(prefix="/jobs", tags=["Jobs"])


def permitted_jobs(request: Request) -> ColumnElement[bool]:
    return visible_jobs(request.app.state.app_env)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    engine = cast(AsyncEngine, request.app.state.engine)
    try:
        async with async_sessionmaker(engine)() as session:
            yield session
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Job storage unavailable") from exc


class JobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    company: str | None
    location: str | None
    workplace_type: str
    source: str
    source_job_id: str
    source_url: str
    source_terms_url: str | None
    published_at: datetime | None
    fetched_at: datetime
    usage_scope: Literal["local_research_only", "source_terms_apply"] = "source_terms_apply"

    @model_validator(mode="after")
    def set_usage_scope(self) -> Self:
        self.usage_scope = (
            "local_research_only"
            if local_research_only(self.source, self.source_url)
            else "source_terms_apply"
        )
        return self


class JobPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[JobSummary]


class SkillEvidence(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    skill_slug: str
    source_field: str
    evidence: str
    start_offset: int
    end_offset: int
    confidence: float
    method: str


class JobDetail(JobSummary):
    description: str
    structured_skills: list[str]
    content_hash: str
    skills: list[SkillEvidence]
    note: str = (
        "Skills are literal mentions, not proof of required proficiency. "
        "Attribution: see source and source_url."
    )


@router.get("", response_model=JobPage)
async def list_jobs(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> JobPage:
    allowed = permitted_jobs(request)
    total = await session.scalar(select(func.count()).select_from(Job).where(allowed)) or 0
    jobs = (
        await session.scalars(
            select(Job)
            .where(allowed)
            .order_by(Job.created_at.desc(), Job.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return JobPage(
        total=total,
        limit=limit,
        offset=offset,
        items=[JobSummary.model_validate(job) for job in jobs],
    )


@router.get("/{job_id}", response_model=JobDetail)
async def job_detail(
    request: Request, job_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]
) -> JobDetail:
    job = await session.scalar(select(Job).where(Job.id == job_id, permitted_jobs(request)))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    skills = (
        await session.scalars(
            select(JobSkill)
            .where(JobSkill.job_id == job_id, JobSkill.content_hash == job.content_hash)
            .order_by(JobSkill.skill_slug)
        )
    ).all()
    return JobDetail(
        **JobSummary.model_validate(job).model_dump(),
        description=job.description,
        structured_skills=job.structured_skills,
        content_hash=job.content_hash,
        skills=[SkillEvidence.model_validate(skill) for skill in skills],
    )
