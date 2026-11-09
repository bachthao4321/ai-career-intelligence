import asyncio
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.models import Job
from app.main import create_app
from tests.db_helpers import migrate

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Set RUN_INTEGRATION=1"),
]


async def test_migration_round_trip_and_model_drift(
    isolated_database: tuple[AsyncEngine, str],
) -> None:
    engine, url = isolated_database
    await asyncio.to_thread(migrate, url, "check")
    await asyncio.to_thread(migrate, url, "upgrade", "head")
    async with engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0006_llm_runtime"
        )
        columns = await connection.run_sync(lambda conn: inspect(conn).get_columns("jobs"))
        assert {column["name"] for column in columns} >= {
            "id",
            "source",
            "content_hash",
            "raw_payload",
        }
    await asyncio.to_thread(migrate, url, "downgrade", "base")
    async with engine.connect() as connection:
        assert not await connection.run_sync(lambda conn: inspect(conn).has_table("jobs"))
    await asyncio.to_thread(migrate, url, "upgrade", "head")
    await asyncio.to_thread(migrate, url, "check")


async def test_jobs_persist_and_enforce_source_identity(
    isolated_database: tuple[AsyncEngine, str],
) -> None:
    engine, _ = isolated_database
    job = {
        "source": "synthetic-fixture",
        "source_job_id": "job-1",
        "source_url": "https://example.com/jobs/1",
        "title": "Python Developer",
        "description": "Python and PostgreSQL",
        "content_hash": "a" * 64,
    }
    async with engine.begin() as connection:
        job_id = await connection.scalar(Job.__table__.insert().values(**job).returning(Job.id))
        assert isinstance(job_id, uuid.UUID)
    async with engine.connect() as connection:
        stored = (
            (await connection.execute(select(Job.__table__).where(Job.id == job_id)))
            .mappings()
            .one()
        )
        assert stored["raw_payload"] == {}
        assert stored["created_at"].tzinfo is not None
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(Job.__table__.insert().values(**job))
    for invalid in [{"title": " "}, {"description": ""}, {"content_hash": "bad-hash"}]:
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    Job.__table__.insert().values(
                        **{**job, "source_job_id": uuid.uuid4().hex, **invalid}
                    )
                )


def test_readiness_against_real_dependencies() -> None:
    with TestClient(create_app(Settings())) as client:
        response = client.get("/ready")
        assert response.status_code == 200, response.text
        assert response.json()["dependencies"] == {"postgres": "ok", "qdrant": "ok"}
