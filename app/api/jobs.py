import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Annotated, Literal, Self, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import Job, JobFact, JobSkill
from app.db.visibility import visible_jobs
from app.ingestion.description_translation import (
    DescriptionTranslator,
    Language,
    Target,
    detect_language,
)
from app.ingestion.policies import local_research_only
from app.ingestion.presentation import JobPresentation, presentations
from app.ingestion.reviewed_titles import REVIEWED_TITLES
from app.security.demo import paid_client_gate

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
    requirement: str = "unspecified"
    requirement_cue: str | None = None


class JobFactsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    version: str
    seniority: str = "unknown"
    seniority_evidence: str | None = None
    experience_years_min: int | None = None
    experience_evidence: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    salary_evidence: str | None = None
    application_deadline: date | None = None


class JobDetail(JobSummary):
    description: str
    description_language: Language
    structured_skills: list[str]
    content_hash: str
    skills: list[SkillEvidence]
    facts: JobFactsOut | None = None
    note: str = (
        "Skills are literal mentions, not proof of required proficiency. Requirement levels "
        "come from nearby wording or section headings; 'unspecified' means no explicit cue. "
        "Facts are copied from explicit phrases (see *_evidence); empty means not stated. "
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


@router.get("/presentation", response_model=dict[str, JobPresentation])
async def job_presentations(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ids: Annotated[list[uuid.UUID], Query(min_length=1, max_length=20)],
) -> dict[str, JobPresentation]:
    jobs = (
        await session.scalars(select(Job).where(Job.id.in_(ids), permitted_jobs(request)))
    ).all()
    return await presentations(session, list(jobs), request.app.state.settings)


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
    facts = await session.scalar(
        select(JobFact).where(JobFact.job_id == job_id, JobFact.content_hash == job.content_hash)
    )
    return JobDetail(
        **JobSummary.model_validate(job).model_dump(),
        facts=JobFactsOut.model_validate(facts) if facts else None,
        description=job.description,
        description_language=detect_language(job.description, job.language),
        structured_skills=job.structured_skills,
        content_hash=job.content_hash,
        skills=[SkillEvidence.model_validate(skill) for skill in skills],
    )


class DescriptionRequest(BaseModel):
    target: Target


class DescriptionResponse(BaseModel):
    title: str
    title_source: Literal["reviewed", "machine"]
    text: str
    target: Target


@router.post("/{job_id}/description-translation", response_model=DescriptionResponse)
async def translate_description(
    request: Request,
    job_id: uuid.UUID,
    body: DescriptionRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DescriptionResponse:
    job = await session.scalar(select(Job).where(Job.id == job_id, permitted_jobs(request)))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    await paid_client_gate(request, request.app.state.settings.agent_model_enabled)
    translator = cast(DescriptionTranslator, request.app.state.description_translator)
    try:
        reviewed = REVIEWED_TITLES.get(job.title)
        if reviewed:
            translated_title = reviewed[0 if body.target == "en" else 1]
            title_source: Literal["reviewed", "machine"] = "reviewed"
        else:
            translated_title = await translator.translate(
                job.title, body.target, job.source, job.source_url
            )
            title_source = "machine"
        translated = await translator.translate(
            job.description, body.target, job.source, job.source_url
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return DescriptionResponse(
        title=translated_title, title_source=title_source, text=translated, target=body.target
    )
