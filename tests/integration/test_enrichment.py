import os

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.db.models import JobFact, JobSkill
from app.ingestion.enrichment import enrich_existing
from app.ingestion.service import import_records
from app.schemas.ingestion import JobRecord

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Set RUN_INTEGRATION=1"),
]


async def requirements(engine: AsyncEngine) -> dict[str, str]:
    async with async_sessionmaker(engine)() as session:
        rows = (await session.execute(select(JobSkill.skill_slug, JobSkill.requirement))).all()
    return dict(rows)


async def test_import_stores_requirement_levels_and_enrich_backfills_them(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> None:
    engine, _ = isolated_database
    await import_records(engine, [record], settings)
    expected = {
        "python": "required",
        "postgresql": "required",
        "kubernetes": "required",
        "javascript": "negated",
    }
    assert await requirements(engine) == expected
    # Rows written before migration 0009 carry the column default until backfilled.
    async with async_sessionmaker(engine).begin() as session:
        await session.execute(
            update(JobSkill).values(requirement="unspecified", requirement_cue=None)
        )
    result = await enrich_existing(engine)
    assert result["changed_skills"] == 4
    assert await requirements(engine) == expected
    assert (await enrich_existing(engine))["changed_skills"] == 0


async def test_import_stores_job_facts_and_enrich_backfills_them(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> None:
    engine, _ = isolated_database
    detailed = record.model_copy(
        update={
            "title": "Senior Backend Developer",
            "description": "Yêu cầu\n- 3 năm kinh nghiệm Python.\nMức lương: 20-30 triệu/tháng.",
        }
    )
    await import_records(engine, [detailed], settings)

    async def facts() -> JobFact:
        async with async_sessionmaker(engine)() as session:
            return (await session.scalars(select(JobFact))).one()

    stored = await facts()
    assert (stored.seniority, stored.experience_years_min) == ("senior", 3)
    assert (stored.salary_min, stored.salary_max, stored.salary_currency) == (
        20_000_000,
        30_000_000,
        "VND",
    )
    assert stored.content_hash == detailed.content_hash
    # Jobs imported before migration 0010 have no facts row until backfilled.
    async with async_sessionmaker(engine).begin() as session:
        await session.execute(delete(JobFact))
    assert (await enrich_existing(engine))["changed_facts"] == 1
    assert (await facts()).salary_period == "month"
    assert (await enrich_existing(engine))["changed_facts"] == 0
