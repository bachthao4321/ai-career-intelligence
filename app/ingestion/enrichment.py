"""Recompute derived, evidence-grounded job metadata for rows already in the database.

Import computes the same values for new or changed jobs; this backfill exists so a new
classifier version can be applied without refetching sources or re-running extraction.
"""

from typing import cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.db.models import Job, JobSkill
from app.ingestion.contracts import EvidenceField
from app.ingestion.requirements import REQUIREMENT_VERSION, classify_requirement


async def enrich_existing(engine: AsyncEngine, *, batch_size: int = 200) -> dict[str, object]:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    counts = {"jobs": 0, "skills": 0, "changed_skills": 0}
    async with sessions() as session:
        job_ids = (await session.scalars(select(Job.id).order_by(Job.id))).all()
    for offset in range(0, len(job_ids), batch_size):
        async with sessions.begin() as session:
            jobs = (
                await session.scalars(
                    select(Job).where(Job.id.in_(job_ids[offset : offset + batch_size]))
                )
            ).all()
            for job in jobs:
                counts["jobs"] += 1
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
    return {**counts, "requirement_version": REQUIREMENT_VERSION}
