from collections import defaultdict
from typing import cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.db.models import Job, JobSkill
from app.ingestion.extraction import inside_link_or_email
from app.ingestion.requirements import as_requirement
from app.repositories.jobs import filtered_jobs
from app.schemas.ingestion import EvidenceField
from app.schemas.search import SearchDocument, SearchFilters, SourceSkillEvidence


class CorpusTooLarge(RuntimeError):
    pass


async def load_documents(
    engine: AsyncEngine,
    settings: Settings,
    filters: SearchFilters | None = None,
) -> list[SearchDocument]:
    statement = filtered_jobs(settings.app_env, filters or SearchFilters())
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
                "Corpus limit exceeded; narrow filters or use a larger-scale index"
            )
        if not jobs:
            return []
        rows = (
            await session.scalars(
                select(JobSkill)
                .join(Job, Job.id == JobSkill.job_id)
                .where(
                    Job.id.in_([job.id for job in jobs]),
                    JobSkill.content_hash == Job.content_hash,
                )
            )
        ).all()
        skills = defaultdict(list)
        evidence: dict[object, list[SourceSkillEvidence]] = defaultdict(list)
        by_id = {job.id: job for job in jobs}
        for row in rows:
            skills[row.job_id].append(row.skill_slug)
            job = by_id[row.job_id]
            fields = {
                "title": job.title,
                "description": job.description,
                "structured_skills": "\n".join(job.structured_skills),
            }
            value = fields.get(row.source_field, "")
            if (
                row.source_field in fields
                and 0 <= row.start_offset < row.end_offset <= len(value)
                and value[row.start_offset : row.end_offset] == row.evidence
                and not inside_link_or_email(value, row.start_offset, row.end_offset)
            ):
                evidence[row.job_id].append(
                    SourceSkillEvidence(
                        slug=row.skill_slug,
                        source_field=cast(EvidenceField, row.source_field),
                        start=row.start_offset,
                        end=row.end_offset,
                        text=row.evidence,
                        requirement=as_requirement(row.requirement),
                        requirement_cue=row.requirement_cue,
                    )
                )
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
                    skill_evidence=tuple(sorted(evidence[job.id], key=lambda item: item.slug)),
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
