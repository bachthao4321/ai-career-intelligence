import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.db.models import AliasCandidate, Job, SkillAlias
from app.ingestion.contracts import JobRecord
from app.ingestion.service import import_records, review_alias

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Set RUN_INTEGRATION=1"),
]


async def test_stale_candidate_cannot_be_approved_and_rejection_does_not_add_alias(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> None:
    engine, _ = isolated_database
    original = record.model_copy(update={"description": "Kube"})
    await import_records(engine, [original], settings)
    async with async_sessionmaker(engine).begin() as session:
        job_id = await session.scalar(select(Job.id))
        candidate = AliasCandidate(
            normalized_alias="kube",
            surface="Kube",
            skill_slug="kubernetes",
            job_id=job_id,
            content_hash=original.content_hash,
            confidence=0.99,
            mention={
                "skill": "kubernetes",
                "source_field": "description",
                "evidence": "Kube",
                "start": 0,
                "end": 4,
                "confidence": 0.99,
            },
        )
        session.add(candidate)
        await session.flush()
        candidate_id = candidate.id
    await import_records(engine, [record], settings)
    with pytest.raises(ValueError, match="stale"):
        await review_alias(engine, candidate_id, "approve", "synthetic-test-reviewer")
    await review_alias(engine, candidate_id, "reject", "synthetic-test-reviewer")
    async with async_sessionmaker(engine)() as session:
        assert await session.get(SkillAlias, "kube") is None
        assert (await session.get(AliasCandidate, candidate_id)).status == "rejected"
