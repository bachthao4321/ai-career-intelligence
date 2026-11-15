from datetime import datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.jobs import JobSummary, get_session
from app.core.config import Settings
from app.db.models import Job, JobSkill, Skill
from app.repositories.jobs import visible_jobs
from app.schemas.cv import MAX_PAGES, MAX_PDF_BYTES
from app.schemas.search import SearchFilters
from app.services.search.documents import filtered_jobs

router = APIRouter(prefix="/platform", tags=["Platform"])


class SourceCount(BaseModel):
    source: str
    count: int


class SkillCount(BaseModel):
    slug: str
    name: str
    count: int


class Overview(BaseModel):
    listings: int
    unique_documents: int
    sources: list[SourceCount]
    skills: list[SkillCount]
    latest_fetch: datetime | None
    scope: Literal["local_research", "public_sources"]
    model_planner_enabled: bool
    cv_ttl_seconds: int
    cv_max_bytes: int = MAX_PDF_BYTES
    cv_max_pages: int = MAX_PAGES


class BrowseJob(JobSummary):
    excerpt: str
    skills: list[str]
    duplicate_listings: int


class BrowsePage(BaseModel):
    total_listings: int
    total: int
    limit: int
    offset: int
    items: list[BrowseJob]


class BrowseParams(SearchFilters):
    limit: int = Field(default=8, ge=1, le=20)
    offset: int = Field(default=0, ge=0, le=10000)


@router.get("/overview", response_model=Overview)
async def overview(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> Overview:
    config = cast(Settings, request.app.state.settings)
    allowed = visible_jobs(config.app_env)
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    count, unique, latest = (
        await session.execute(
            select(
                func.count(Job.id),
                func.count(func.distinct(Job.content_hash)),
                func.max(Job.fetched_at),
            ).where(allowed)
        )
    ).one()
    sources = (
        await session.execute(
            select(Job.source, func.count(Job.id))
            .where(allowed)
            .group_by(Job.source)
            .order_by(func.count(Job.id).desc(), Job.source)
        )
    ).all()
    skills = (
        await session.execute(
            select(Skill.slug, Skill.name, func.count(func.distinct(JobSkill.job_id)))
            .join(JobSkill, JobSkill.skill_slug == Skill.slug)
            .join(Job, Job.id == JobSkill.job_id)
            .where(allowed, JobSkill.content_hash == Job.content_hash)
            .group_by(Skill.slug, Skill.name)
            .order_by(func.count(func.distinct(JobSkill.job_id)).desc(), Skill.slug)
        )
    ).all()
    return Overview(
        listings=count,
        unique_documents=unique,
        latest_fetch=latest,
        sources=[SourceCount(source=source, count=n) for source, n in sources],
        skills=[SkillCount(slug=slug, name=name, count=n) for slug, name, n in skills],
        scope="local_research" if config.app_env == "local" else "public_sources",
        model_planner_enabled=config.agent_model_enabled and config.agent_daily_call_limit > 0,
        cv_ttl_seconds=config.cv_ttl_seconds,
    )


@router.get("/jobs", response_model=BrowsePage)
async def browse_jobs(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    params: Annotated[BrowseParams, Query()],
) -> BrowsePage:
    limit, offset = params.limit, params.offset
    filters = SearchFilters.model_validate(params.model_dump(exclude={"limit", "offset"}))
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    eligible = filtered_jobs(request.app.state.app_env, filters).subquery()
    n, unique = (
        await session.execute(
            select(func.count(), func.count(func.distinct(eligible.c.content_hash))).select_from(
                eligible
            )
        )
    ).one()
    ranked = select(
        eligible.c.id,
        func.row_number()
        .over(
            partition_by=eligible.c.content_hash,
            order_by=(eligible.c.source, eligible.c.source_job_id),
        )
        .label("position"),
        func.count().over(partition_by=eligible.c.content_hash).label("duplicates"),
    ).subquery()
    rows = (
        await session.execute(
            select(Job, ranked.c.duplicates)
            .join(ranked, ranked.c.id == Job.id)
            .where(ranked.c.position == 1)
            .order_by(Job.fetched_at.desc(), Job.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    current = (
        (
            await session.execute(
                select(JobSkill.job_id, JobSkill.skill_slug)
                .join(Job, Job.id == JobSkill.job_id)
                .where(
                    JobSkill.job_id.in_([job.id for job, _ in rows]),
                    JobSkill.content_hash == Job.content_hash,
                )
            )
        ).all()
        if rows
        else []
    )
    skills: dict[object, list[str]] = {}
    for job_id, slug in current:
        skills.setdefault(job_id, []).append(slug)
    return BrowsePage(
        total_listings=n,
        total=unique,
        limit=limit,
        offset=offset,
        items=[
            BrowseJob(
                **JobSummary.model_validate(job).model_dump(),
                excerpt=job.description[:240],
                skills=sorted(skills.get(job.id, [])),
                duplicate_listings=duplicates,
            )
            for job, duplicates in rows
        ],
    )
