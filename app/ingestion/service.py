import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    AliasCandidate,
    ExtractionRun,
    Job,
    JobSkill,
    JobSnapshot,
    Skill,
    SkillAlias,
)
from app.ingestion.contracts import JobRecord, SkillMention, alias_key, json_hash
from app.ingestion.extraction import (
    EXTRACTION_VERSION,
    SkillCatalog,
    deterministic_extract,
    validate_mentions,
)
from app.ingestion.llm import DeepSeekExtractor, ExtractionError, reserve_daily_call


@dataclass
class ImportReport:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    extraction_cached: int = 0
    extracted: int = 0
    fallback: int = 0
    accepted_skills: int = 0
    alias_candidates: int = 0
    rejected_mentions: int = 0
    llm_calls: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


async def seed_catalog(session: AsyncSession) -> SkillCatalog:
    catalog = SkillCatalog.load()
    await session.execute(
        insert(Skill)
        .values(
            [
                {"slug": definition.slug, "name": definition.name}
                for definition in catalog.definitions
            ]
        )
        .on_conflict_do_nothing()
    )
    await session.execute(
        insert(SkillAlias)
        .values(
            [
                {
                    "normalized_alias": key,
                    "surface": catalog.surfaces[key],
                    "skill_slug": slug,
                    "origin": "curated",
                }
                for key, slug in catalog.aliases.items()
            ]
        )
        .on_conflict_do_nothing()
    )
    rows = (await session.execute(select(SkillAlias))).scalars().all()
    for row in rows:
        if (
            row.normalized_alias in catalog.aliases
            and catalog.aliases[row.normalized_alias] != row.skill_slug
        ):
            raise ValueError("Stored aliases conflict with the curated catalog")
    return SkillCatalog.load(
        {row.surface: row.skill_slug for row in rows if row.origin == "reviewed"}
    )


async def import_records(
    engine: AsyncEngine,
    records: list[JobRecord],
    settings: Settings,
    *,
    extractor: DeepSeekExtractor | None = None,
    max_calls: int = 0,
    retry_failed: bool = False,
) -> ImportReport:
    if not 0 <= max_calls <= 100:
        raise ValueError("max_calls must be between 0 and 100")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    report = ImportReport()
    async with sessions.begin() as session:
        catalog = await seed_catalog(session)
    version = extractor.version if extractor else f"deterministic:{EXTRACTION_VERSION}"
    for record in records:
        # One transaction per source identity: concurrent reruns wait on this row lock.
        async with sessions.begin() as session:
            snapshot = {
                "record": record.model_dump(mode="json", exclude={"raw_payload", "fetched_at"}),
                "raw": record.raw_payload,
            }
            values = record.model_dump(
                mode="json", exclude={"raw_payload", "published_at", "fetched_at"}
            )
            values.update(
                raw_payload=record.raw_payload,
                raw_hash=json_hash(snapshot),
                content_hash=record.content_hash,
                published_at=record.published_at,
                fetched_at=record.fetched_at,
            )
            created_id = await session.scalar(
                insert(Job)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[Job.source, Job.source_job_id])
                .returning(Job.id)
            )
            job = (
                await session.execute(
                    select(Job)
                    .where(Job.source == record.source, Job.source_job_id == record.source_job_id)
                    .with_for_update()
                )
            ).scalar_one()
            changed = (
                job.content_hash != record.content_hash
                or job.raw_hash != values["raw_hash"]
                or job.source_url != str(record.source_url)
            )
            if created_id is not None:
                report.inserted += 1
            elif changed:
                report.updated += 1
            else:
                report.unchanged += 1
            await session.execute(
                update(Job)
                .where(Job.id == job.id)
                .values(**values, updated_at=datetime.now(UTC) if changed else job.updated_at)
            )
            await session.execute(
                insert(JobSnapshot)
                .values(
                    job_id=job.id,
                    raw_hash=values["raw_hash"],
                    payload=snapshot,
                    source_url=str(record.source_url),
                    source_terms_url=str(record.source_terms_url),
                    fetched_at=record.fetched_at,
                )
                .on_conflict_do_nothing(constraint="uq_job_snapshots_job_raw_hash")
            )
            cached = (
                await session.execute(
                    select(ExtractionRun).where(
                        ExtractionRun.job_id == job.id,
                        ExtractionRun.content_hash == record.content_hash,
                        ExtractionRun.extractor_version == version,
                        ExtractionRun.catalog_version == catalog.version,
                    )
                )
            ).scalar_one_or_none()
            if cached and not (retry_failed and cached.status == "fallback"):
                # Restore this version's evidence if the content reverted to an older snapshot.
                mentions = [
                    SkillMention.model_validate(value) for value in cached.result["accepted"]
                ]
                await _replace_skills(
                    session, job.id, cached.id, record, mentions, cached.result["methods"]
                )
                report.extraction_cached += 1
                continue
            baseline = deterministic_extract(record, catalog)
            mentions = list(baseline)
            usage: dict[str, Any] = {}
            error: str | None = None
            if extractor:
                if report.llm_calls >= max_calls:
                    error = "import_call_limit"
                elif record.input_characters > 30_000:
                    error = "input_too_long"
                elif not await reserve_daily_call(engine, settings.deepseek_daily_call_limit):
                    error = "daily_call_limit"
                else:
                    report.llm_calls += 1
                    try:
                        output, usage = await extractor.extract(record, catalog)
                        mentions.extend(output.skills)
                    except ExtractionError as exc:
                        error = str(exc)
            grounded = validate_mentions(record, mentions, catalog)
            baseline_skills = {mention.skill for mention in baseline}
            methods = {
                mention.skill: "deterministic" if mention.skill in baseline_skills else "deepseek"
                for mention in grounded.accepted
            }
            result = {
                "accepted": [mention.model_dump() for mention in grounded.accepted],
                "methods": methods,
                "rejected": grounded.rejected,
                "alias_candidates": [mention.model_dump() for mention in grounded.alias_candidates],
                "error": error,
                "attempts": (cached.result.get("attempts", 0) if cached else 0) + 1,
            }
            if cached:
                run_id = cached.id
                await session.execute(
                    update(ExtractionRun)
                    .where(ExtractionRun.id == run_id)
                    .values(status="fallback" if error else "completed", result=result, usage=usage)
                )
            else:
                run_id = uuid.uuid4()
                session.add(
                    ExtractionRun(
                        id=run_id,
                        job_id=job.id,
                        content_hash=record.content_hash,
                        extractor_version=version,
                        catalog_version=catalog.version,
                        status="fallback" if error else "completed",
                        result=result,
                        usage=usage,
                    )
                )
                await session.flush()
            await _replace_skills(session, job.id, run_id, record, grounded.accepted, methods)
            for candidate in grounded.alias_candidates:
                proposed = insert(AliasCandidate).values(
                    normalized_alias=alias_key(candidate.evidence),
                    surface=candidate.evidence,
                    skill_slug=candidate.skill,
                    job_id=job.id,
                    content_hash=record.content_hash,
                    mention=candidate.model_dump(),
                    confidence=candidate.confidence,
                )
                await session.execute(
                    proposed.on_conflict_do_update(
                        constraint="uq_alias_candidates_alias_skill",
                        set_={
                            key: getattr(proposed.excluded, key)
                            for key in (
                                "surface",
                                "job_id",
                                "content_hash",
                                "mention",
                                "confidence",
                            )
                        },
                        where=AliasCandidate.status == "pending",
                    )
                )
            report.extracted += 1
            report.fallback += int(error is not None)
            report.accepted_skills += len(grounded.accepted)
            report.alias_candidates += len(grounded.alias_candidates)
            report.rejected_mentions += len(grounded.rejected)
    return report


async def _replace_skills(
    session: AsyncSession,
    job_id: uuid.UUID,
    run_id: uuid.UUID,
    record: JobRecord,
    mentions: list[SkillMention],
    methods: dict[str, str],
) -> None:
    await session.execute(delete(JobSkill).where(JobSkill.job_id == job_id))
    for mention in mentions:
        session.add(
            JobSkill(
                job_id=job_id,
                skill_slug=mention.skill,
                extraction_run_id=run_id,
                content_hash=record.content_hash,
                source_field=mention.source_field,
                evidence=mention.evidence,
                start_offset=mention.start,
                end_offset=mention.end,
                confidence=mention.confidence,
                method=methods[mention.skill],
            )
        )


async def review_alias(
    engine: AsyncEngine,
    candidate_id: uuid.UUID,
    decision: Literal["approve", "reject"],
    reviewer: str,
) -> None:
    if not reviewer.strip() or len(reviewer) > 100:
        raise ValueError("A reviewer name of 1–100 characters is required")
    async with async_sessionmaker(engine).begin() as session:
        candidate = (
            await session.execute(
                select(AliasCandidate).where(AliasCandidate.id == candidate_id).with_for_update()
            )
        ).scalar_one_or_none()
        if candidate is None or candidate.status != "pending":
            raise ValueError("Candidate not found or already reviewed")
        if decision == "approve":
            # The original evidence must still refer to the current content version.
            job = await session.get(Job, candidate.job_id)
            if job is None or job.content_hash != candidate.content_hash:
                raise ValueError("Candidate evidence is stale; re-extract before review")
            await session.execute(
                insert(SkillAlias)
                .values(
                    normalized_alias=candidate.normalized_alias,
                    surface=candidate.surface,
                    skill_slug=candidate.skill_slug,
                    origin="reviewed",
                    reviewed_by=reviewer.strip(),
                )
                .on_conflict_do_nothing()
            )
            stored = await session.get(SkillAlias, candidate.normalized_alias)
            if stored is None or stored.skill_slug != candidate.skill_slug:
                raise ValueError("Alias conflicts with an existing canonical mapping")
        candidate.status = "approved" if decision == "approve" else "rejected"
        candidate.reviewed_by = reviewer.strip()
        candidate.reviewed_at = datetime.now(UTC)
