from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.db.models import AliasCandidate, ExtractionRun, Job, JobSkill, JobSnapshot
from app.ingestion.contracts import EvidenceField


async def build_report(engine: AsyncEngine, *, sample_size: int = 10) -> dict[str, Any]:
    if not 1 <= sample_size <= 50:
        raise ValueError("sample_size must be between 1 and 50")
    async with async_sessionmaker(engine)() as session:
        jobs = (await session.scalars(select(Job).order_by(Job.source, Job.source_job_id))).all()
        skills = (
            await session.scalars(select(JobSkill).order_by(JobSkill.job_id, JobSkill.skill_slug))
        ).all()
        by_id = {job.id: job for job in jobs}
        evidence_errors: list[dict[str, str]] = []
        samples: list[dict[str, Any]] = []
        by_job: dict[str, list[dict[str, Any]]] = {}
        skill_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        for job in jobs:
            source_counts[job.source] = source_counts.get(job.source, 0) + 1
        for skill in skills:
            job = by_id[skill.job_id]
            text = (
                "\n".join(job.structured_skills)
                if skill.source_field == "structured_skills"
                else getattr(job, skill.source_field)
            )
            valid = (
                skill.content_hash == job.content_hash
                and 0 <= skill.start_offset < skill.end_offset <= len(text)
                and text[skill.start_offset : skill.end_offset] == skill.evidence
            )
            if not valid:
                evidence_errors.append({"job_id": str(job.id), "skill": skill.skill_slug})
            skill_counts[skill.skill_slug] = skill_counts.get(skill.skill_slug, 0) + 1
            by_job.setdefault(str(job.id), []).append(
                {
                    "skill": skill.skill_slug,
                    "source_field": cast(EvidenceField, skill.source_field),
                    "evidence": skill.evidence,
                    "start": skill.start_offset,
                    "end": skill.end_offset,
                    "context": text[max(0, skill.start_offset - 75) : skill.end_offset + 75],
                    "literal_check": valid,
                }
            )
        # Evenly spaced source IDs produce a deterministic, reproducible review sample.
        sample_jobs = (
            [
                jobs[index]
                for index in sorted(
                    {
                        int(i * len(jobs) / min(sample_size, len(jobs)))
                        for i in range(min(sample_size, len(jobs)))
                    }
                )
            ]
            if jobs
            else []
        )
        for job in sample_jobs:
            samples.append(
                {
                    "job_id": str(job.id),
                    "source": job.source,
                    "source_job_id": job.source_job_id,
                    "source_url": job.source_url,
                    "title": job.title,
                    "skills": by_job.get(str(job.id), []),
                    "review_status": "requires_manual_review",
                }
            )
        duplicates = (
            await session.execute(
                select(Job.content_hash, func.count())
                .group_by(Job.content_hash)
                .having(func.count() > 1)
            )
        ).all()
        pending = (
            await session.scalar(
                select(func.count())
                .select_from(AliasCandidate)
                .where(AliasCandidate.status == "pending")
            )
            or 0
        )
        return {
            "jobs": len(jobs),
            "sources": source_counts,
            "skill_assignments": len(skills),
            "jobs_without_curated_mentions": sum(str(job.id) not in by_job for job in jobs),
            "top_skills": sorted(skill_counts.items(), key=lambda pair: (-pair[1], pair[0])),
            "literal_evidence_errors": evidence_errors,
            "exact_content_duplicate_groups": len(duplicates),
            "distinct_content_hashes": len({job.content_hash for job in jobs}),
            "snapshots": await session.scalar(select(func.count()).select_from(JobSnapshot)),
            "extraction_runs": await session.scalar(
                select(func.count()).select_from(ExtractionRun)
            ),
            "pending_alias_candidates": pending,
            "samples": samples,
            "limitations": [
                "Literal evidence checks do not measure semantic precision or recall.",
                "Mentions may be optional, historical or negated; requirement strength is unknown.",
                "An absent skill means not detected, not proof the job does not require it.",
                "No live paid model evaluation is implied by this report.",
            ],
        }
