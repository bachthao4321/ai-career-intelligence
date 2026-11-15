"""Recompute derived, evidence-grounded job metadata for rows already in the database.

Import computes the same values for new or changed jobs; this backfill exists so a new
classifier version can be applied without refetching sources or re-running extraction.
"""

from typing import cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.db.models import Job, JobFact, JobSkill
from app.ingestion.facts import FACTS_VERSION, JobFacts, facts_from
from app.ingestion.requirements import REQUIREMENT_VERSION, classify_requirement
from app.ingestion.service import upsert_facts
from app.schemas.ingestion import EvidenceField


async def enrich_existing(engine: AsyncEngine, *, batch_size: int = 200) -> dict[str, object]:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    counts = {"jobs": 0, "skills": 0, "changed_skills": 0, "changed_facts": 0}
    async with sessions() as session:
        job_ids = (await session.scalars(select(Job.id).order_by(Job.id))).all()
    for offset in range(0, len(job_ids), batch_size):
        async with sessions.begin() as session:
            jobs = (
                await session.scalars(
                    select(Job).where(Job.id.in_(job_ids[offset : offset + batch_size]))
                )
            ).all()
            stored = {
                row.job_id: row
                for row in await session.scalars(
                    select(JobFact).where(JobFact.job_id.in_([job.id for job in jobs]))
                )
            }
            for job in jobs:
                counts["jobs"] += 1
                facts = facts_from(job.title, job.description, job.source, job.raw_payload)
                current = stored.get(job.id)
                if (
                    current is None
                    or current.content_hash != job.content_hash
                    or (JobFacts.model_validate(current, from_attributes=True) != facts)
                ):
                    counts["changed_facts"] += 1
                    await upsert_facts(session, job.id, job.content_hash, facts)
                texts = {
                    "title": job.title,
                    "description": job.description,
                    "structured_skills": "\n".join(job.structured_skills),
                }
                rows = (
                    await session.scalars(
                        select(JobSkill).where(
                            JobSkill.job_id == job.id, JobSkill.content_hash == job.content_hash
                        )
                    )
                ).all()
                for row in rows:
                    counts["skills"] += 1
                    text = texts.get(row.source_field, "")
                    if text[row.start_offset : row.end_offset] != row.evidence:
                        continue
                    level = classify_requirement(
                        cast(EvidenceField, row.source_field),
                        text,
                        row.start_offset,
                        row.end_offset,
                    )
                    if (row.requirement, row.requirement_cue) != (level.requirement, level.cue):
                        counts["changed_skills"] += 1
                        await session.execute(
                            update(JobSkill)
                            .where(
                                JobSkill.job_id == row.job_id,
                                JobSkill.skill_slug == row.skill_slug,
                            )
                            .values(requirement=level.requirement, requirement_cue=level.cue)
                        )
    return {
        **counts,
        "requirement_version": REQUIREMENT_VERSION,
        "facts_version": FACTS_VERSION,
    }
