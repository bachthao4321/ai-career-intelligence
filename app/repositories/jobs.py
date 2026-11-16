"""Job queries. Every read goes through `visible_jobs` so source scope is enforced in one place."""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Row, Select, and_, func, select, text, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.policies import LOCAL_ONLY_SOURCES, LOCAL_ONLY_URL_PATTERN
from app.db.models import Job, JobFact, JobSkill, Skill
from app.schemas.search import SearchFilters


def visible_jobs(environment: str) -> ColumnElement[bool]:
    if environment == "local":
        return true()
    return and_(
        Job.source.not_in(LOCAL_ONLY_SOURCES),
        ~Job.source_url.op("~*")(LOCAL_ONLY_URL_PATTERN),
    )


def filtered_jobs(environment: str, filters: SearchFilters) -> Select[tuple[Job]]:
    statement = select(Job).where(visible_jobs(environment))
    if filters.source:
        statement = statement.where(Job.source == filters.source)
    if filters.location:
        statement = statement.where(Job.location.icontains(filters.location, autoescape=True))
    if filters.workplace_type:
        statement = statement.where(Job.workplace_type == filters.workplace_type)
    for skill in set(filters.skills):
        statement = statement.where(
            select(JobSkill.job_id)
            .where(
                JobSkill.job_id == Job.id,
                JobSkill.content_hash == Job.content_hash,
                JobSkill.skill_slug == skill,
            )
            .exists()
        )
    return statement


async def read_only_snapshot(session: AsyncSession) -> None:
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))


class JobRepository:
    def __init__(self, session: AsyncSession, environment: str) -> None:
        self.session = session
        self.environment = environment
        self.visible = visible_jobs(environment)

    async def get(self, job_id: uuid.UUID) -> Job | None:
        job: Job | None = await self.session.scalar(
            select(Job).where(Job.id == job_id, self.visible)
        )
        return job

    async def get_many(self, ids: Sequence[uuid.UUID]) -> list[Job]:
        return list(
            (await self.session.scalars(select(Job).where(Job.id.in_(ids), self.visible))).all()
        )

    async def page(self, limit: int, offset: int) -> tuple[int, list[Job]]:
        total = await self.session.scalar(select(func.count()).select_from(Job).where(self.visible))
        jobs = await self.session.scalars(
            select(Job)
            .where(self.visible)
            .order_by(Job.created_at.desc(), Job.id)
            .limit(limit)
            .offset(offset)
        )
        return total or 0, list(jobs.all())

    async def current_skills(self, job: Job) -> list[JobSkill]:
        rows = await self.session.scalars(
            select(JobSkill)
            .where(JobSkill.job_id == job.id, JobSkill.content_hash == job.content_hash)
            .order_by(JobSkill.skill_slug)
        )
        return list(rows.all())

    async def current_facts(self, job: Job) -> JobFact | None:
        facts: JobFact | None = await self.session.scalar(
            select(JobFact).where(
                JobFact.job_id == job.id, JobFact.content_hash == job.content_hash
            )
        )
        return facts

    async def totals(self) -> Row[tuple[int, int, datetime]]:
        """Listing count, distinct content count, latest fetch (None when empty)."""
        return (
            await self.session.execute(
                select(
                    func.count(Job.id),
                    func.count(func.distinct(Job.content_hash)),
                    func.max(Job.fetched_at),
                ).where(self.visible)
            )
        ).one()

    async def source_counts(self) -> Sequence[Row[tuple[str, int]]]:
        return (
            await self.session.execute(
                select(Job.source, func.count(Job.id))
                .where(self.visible)
                .group_by(Job.source)
                .order_by(func.count(Job.id).desc(), Job.source)
            )
        ).all()

    async def skill_counts(self) -> Sequence[Row[tuple[str, str, int]]]:
        listings = func.count(func.distinct(JobSkill.job_id))
        return (
            await self.session.execute(
                select(Skill.slug, Skill.name, listings)
                .join(JobSkill, JobSkill.skill_slug == Skill.slug)
                .join(Job, Job.id == JobSkill.job_id)
                .where(self.visible, JobSkill.content_hash == Job.content_hash)
                .group_by(Skill.slug, Skill.name)
                .order_by(listings.desc(), Skill.slug)
            )
        ).all()

    async def browse(
        self, filters: SearchFilters, limit: int, offset: int
    ) -> tuple[int, int, list[tuple[Job, int]]]:
        """One listing per content hash, newest first, with each group's duplicate count."""
        eligible = filtered_jobs(self.environment, filters).subquery()
        listings, unique = (
            await self.session.execute(
                select(
                    func.count(), func.count(func.distinct(eligible.c.content_hash))
                ).select_from(eligible)
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
            await self.session.execute(
                select(Job, ranked.c.duplicates)
                .join(ranked, ranked.c.id == Job.id)
                .where(ranked.c.position == 1)
                .order_by(Job.fetched_at.desc(), Job.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return listings, unique, [(job, duplicates) for job, duplicates in rows]

    async def skill_slugs(self, job_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
        if not job_ids:
            return {}
        rows = await self.session.execute(
            select(JobSkill.job_id, JobSkill.skill_slug)
            .join(Job, Job.id == JobSkill.job_id)
            .where(JobSkill.job_id.in_(job_ids), JobSkill.content_hash == Job.content_hash)
        )
        slugs: dict[uuid.UUID, list[str]] = {}
        for job_id, slug in rows.all():
            slugs.setdefault(job_id, []).append(slug)
        return slugs
