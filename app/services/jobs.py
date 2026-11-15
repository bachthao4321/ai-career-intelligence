"""Job browsing, detail and translation use cases on top of JobRepository."""

import uuid
from collections.abc import Sequence
from typing import Literal, Protocol

from app.core.config import Settings
from app.db.models import Job
from app.repositories.jobs import JobRepository, read_only_snapshot
from app.schemas.jobs import (
    BrowseJob,
    BrowsePage,
    BrowseParams,
    DescriptionResponse,
    JobDetail,
    JobFactsOut,
    JobPage,
    JobPresentation,
    JobSummary,
    SkillEvidence,
    Target,
)
from app.schemas.platform import Overview, SkillCount, SourceCount
from app.schemas.search import SearchFilters
from app.services.translation.descriptions import detect_language
from app.services.translation.presentation import presentations
from app.services.translation.reviewed_titles import REVIEWED_TITLES


class Translator(Protocol):
    async def translate(self, text: str, target: Target, source: str, source_url: str) -> str: ...


class JobNotFound(Exception):
    pass


class JobService:
    def __init__(self, jobs: JobRepository, settings: Settings) -> None:
        self.jobs = jobs
        self.settings = settings

    async def page(self, limit: int, offset: int) -> JobPage:
        total, jobs = await self.jobs.page(limit, offset)
        return JobPage(
            total=total,
            limit=limit,
            offset=offset,
            items=[JobSummary.model_validate(job) for job in jobs],
        )

    async def presentations(self, ids: Sequence[uuid.UUID]) -> dict[str, JobPresentation]:
        jobs = await self.jobs.get_many(ids)
        return await presentations(self.jobs.session, jobs, self.settings)

    async def require(self, job_id: uuid.UUID) -> Job:
        job = await self.jobs.get(job_id)
        if job is None:
            raise JobNotFound
        return job

    async def detail(self, job_id: uuid.UUID) -> JobDetail:
        job = await self.require(job_id)
        skills = await self.jobs.current_skills(job)
        facts = await self.jobs.current_facts(job)
        return JobDetail(
            **JobSummary.model_validate(job).model_dump(),
            facts=JobFactsOut.model_validate(facts) if facts else None,
            description=job.description,
            description_language=detect_language(job.description, job.language),
            structured_skills=job.structured_skills,
            content_hash=job.content_hash,
            skills=[SkillEvidence.model_validate(skill) for skill in skills],
        )

    async def overview(self) -> Overview:
        config = self.settings
        await read_only_snapshot(self.jobs.session)
        listings, unique, latest = await self.jobs.totals()
        sources = await self.jobs.source_counts()
        skills = await self.jobs.skill_counts()
        return Overview(
            listings=listings,
            unique_documents=unique,
            latest_fetch=latest,
            sources=[SourceCount(source=source, count=n) for source, n in sources],
            skills=[SkillCount(slug=slug, name=name, count=n) for slug, name, n in skills],
            scope="local_research" if config.app_env == "local" else "public_sources",
            model_planner_enabled=config.agent_model_enabled and config.agent_daily_call_limit > 0,
            cv_ttl_seconds=config.cv_ttl_seconds,
        )

    async def browse(self, params: BrowseParams) -> BrowsePage:
        filters = SearchFilters.model_validate(params.model_dump(exclude={"limit", "offset"}))
        await read_only_snapshot(self.jobs.session)
        listings, unique, rows = await self.jobs.browse(filters, params.limit, params.offset)
        skills = await self.jobs.skill_slugs([job.id for job, _ in rows])
        return BrowsePage(
            total_listings=listings,
            total=unique,
            limit=params.limit,
            offset=params.offset,
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


async def translate_job(job: Job, target: Target, translator: Translator) -> DescriptionResponse:
    """Translate title and description; reviewed titles skip the model call."""
    reviewed = REVIEWED_TITLES.get(job.title)
    title_source: Literal["reviewed", "machine"]
    if reviewed:
        title = reviewed[0 if target == "en" else 1]
        title_source = "reviewed"
    else:
        title = await translator.translate(job.title, target, job.source, job.source_url)
        title_source = "machine"
    text = await translator.translate(job.description, target, job.source, job.source_url)
    return DescriptionResponse(title=title, title_source=title_source, text=text, target=target)
