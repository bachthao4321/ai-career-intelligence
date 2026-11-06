from collections import defaultdict

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.db.models import Job, JobSkill
from app.db.visibility import visible_jobs
from app.retrieval.contracts import SearchDocument, SearchFilters


class CorpusTooLarge(RuntimeError):
    pass


async def load_documents(
    engine: AsyncEngine,
    settings: Settings,
    filters: SearchFilters | None = None,
) -> list[SearchDocument]:
    filters = filters or SearchFilters()
    statement = select(Job).where(visible_jobs(settings.app_env))
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
    async with async_sessionmaker(engine)() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        jobs = (
            await session.scalars(
                statement.order_by(Job.source, Job.source_job_id).limit(
                    settings.search_corpus_limit + 1
                )
            )
        ).all()
        if len(jobs) > settings.search_corpus_limit:
            raise CorpusTooLarge(
                "M2 corpus limit exceeded; narrow filters or use a larger-scale index"
            )
        if not jobs:
            return []
        rows = (
            await session.execute(
                select(JobSkill.job_id, JobSkill.skill_slug)
                .join(Job, Job.id == JobSkill.job_id)
                .where(
                    Job.id.in_([job.id for job in jobs]),
                    JobSkill.content_hash == Job.content_hash,
                )
            )
        ).all()
        skills = defaultdict(list)
        for job_id, skill_slug in rows:
            skills[job_id].append(skill_slug)
        documents = []
        for job in jobs:
            skill_slugs = tuple(sorted(skills[job.id]))
            full_text = "\n".join(
                value
                for value in (
                    job.title,
                    job.company,
                    job.location,
                    " ".join(job.structured_skills),
                    " ".join(skill_slugs),
                    job.description,
                )
                if value
            )
            documents.append(
                SearchDocument(
                    job_id=job.id,
                    content_hash=job.content_hash,
                    source=job.source,
                    source_job_id=job.source_job_id,
                    source_url=job.source_url,
                    title=job.title,
                    company=job.company,
                    location=job.location,
                    workplace_type=job.workplace_type,
                    text=full_text[:20_000],
                    skills=skill_slugs,
                    truncated=len(full_text) > 20_000,
                )
            )
        return documents


def deduplicate(documents: list[SearchDocument]) -> tuple[list[SearchDocument], dict[str, int]]:
    unique: dict[str, SearchDocument] = {}
    counts: dict[str, int] = defaultdict(int)
    for doc in sorted(documents, key=lambda value: (value.source, value.source_job_id)):
        unique.setdefault(doc.content_hash, doc)
        counts[doc.content_hash] += 1
    return list(unique.values()), dict(counts)
