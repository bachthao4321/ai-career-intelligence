import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings
from app.db.models import Job
from app.main import create_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Set RUN_INTEGRATION=1"),
]


def migrate(url: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
async def isolated_database() -> AsyncIterator[tuple[AsyncEngine, str]]:
    settings = Settings()
    url = settings.database_url.get_secret_value()
    admin_engine = create_async_engine(url)
    schema = "test_m0_" + uuid.uuid4().hex
    isolated_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    isolated_dsn = isolated_url.render_as_string(hide_password=False)
    engine = create_async_engine(isolated_dsn)
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await asyncio.to_thread(migrate, isolated_dsn, "upgrade", "head")
        yield engine, isolated_dsn
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            # Only remove the UUID-named schema created by this fixture.
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


async def test_migration_round_trip_and_model_drift(
    isolated_database: tuple[AsyncEngine, str],
) -> None:
    engine, url = isolated_database
    await asyncio.to_thread(migrate, url, "check")
    await asyncio.to_thread(migrate, url, "upgrade", "head")
    async with engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0001_create_jobs"
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
