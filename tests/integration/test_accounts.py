import asyncio
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.models import Job, SavedJob, User, UserSession
from app.ingestion.service import import_records
from app.main import create_app
from app.schemas.ingestion import JobRecord

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Set RUN_INTEGRATION=1"),
]

ACCOUNT = {"email": "Ana@Example.com", "password": "correct horse battery"}


async def setup(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> tuple[Settings, str, str]:
    engine, url = isolated_database
    config = settings.model_copy(update={"database_url": SecretStr(url), "app_env": "test"})
    public = record.model_copy(update={"source": "jobtech"})
    private = record.model_copy(update={"source": "itviec", "source_job_id": "private"})
    await import_records(engine, [public, private], config.model_copy(update={"app_env": "local"}))
    async with engine.connect() as connection:
        ids = dict((await connection.execute(select(Job.source, Job.id))).tuples().all())
    return config, str(ids["jobtech"]), str(ids["itviec"])


async def test_register_login_save_and_isolation(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> None:
    config, job_id, private_id = await setup(isolated_database, settings, record)
    engine = isolated_database[0]

    def exercise() -> None:
        with TestClient(create_app(config)) as client:
            assert client.get("/auth/me").status_code == 401
            assert client.get("/me/saved-jobs").status_code == 401
            assert client.put(f"/me/saved-jobs/{job_id}").status_code == 401
            bad = {"email": "not-an-email", "password": "long enough"}
            assert client.post("/auth/register", json=bad).status_code == 422
            short = {"email": "a@example.com", "password": "short"}
            assert client.post("/auth/register", json=short).status_code == 422

            created = client.post("/auth/register", json=ACCOUNT)
            assert created.status_code == 201, created.text
            assert created.json()["email"] == "ana@example.com"
            assert "password" not in created.text
            assert created.headers["cache-control"] == "no-store"
            cookie = created.headers["set-cookie"]
            assert "HttpOnly" in cookie and "SameSite=strict" in cookie
            assert client.get("/auth/me").json()["email"] == "ana@example.com"
            again = {**ACCOUNT, "email": "ANA@example.com"}
            assert client.post("/auth/register", json=again).status_code == 409

            assert client.put(f"/me/saved-jobs/{job_id}").status_code == 204
            assert client.put(f"/me/saved-jobs/{job_id}").status_code == 204  # idempotent
            assert client.put(f"/me/saved-jobs/{uuid.uuid4()}").status_code == 404
            # Sources hidden outside local research cannot be saved or listed.
            assert client.put(f"/me/saved-jobs/{private_id}").status_code == 404
            evil = {"Origin": "https://evil.example"}
            assert client.put(f"/me/saved-jobs/{job_id}", headers=evil).status_code == 403
            cross = {"Sec-Fetch-Site": "cross-site"}
            assert client.post("/auth/logout", headers=cross).status_code == 403
            saved = client.get("/me/saved-jobs").json()["items"]
            assert [item["job"]["id"] for item in saved] == [job_id]

            assert client.post("/auth/logout").status_code == 204
            assert client.get("/auth/me").status_code == 401
            wrong = {**ACCOUNT, "password": "wrong password!"}
            assert client.post("/auth/login", json=wrong).status_code == 401
            unknown = {**ACCOUNT, "email": "nobody@example.com"}
            missing = client.post("/auth/login", json=unknown)
            assert missing.status_code == 401
            assert missing.json() == client.post("/auth/login", json=wrong).json()
            assert client.post("/auth/login", json=ACCOUNT).status_code == 200
            assert len(client.get("/me/saved-jobs").json()["items"]) == 1

        with TestClient(create_app(config)) as other:
            other_account = {"email": "bo@example.com", "password": "another secret"}
            assert other.post("/auth/register", json=other_account).status_code == 201
            assert other.get("/me/saved-jobs").json()["items"] == []
            assert other.delete(f"/me/saved-jobs/{job_id}").status_code == 204
            other.cookies.clear()
            other.cookies.set("career_user", "x" * 43)
            assert other.get("/auth/me").status_code == 401

    await asyncio.to_thread(exercise)
    async with engine.connect() as connection:
        stored = await connection.scalar(
            select(User.password_hash).where(User.email == "ana@example.com")
        )
        assert stored is not None and stored.startswith("scrypt$")
        assert ACCOUNT["password"] not in stored
        tokens = (await connection.execute(select(UserSession.token_hash))).scalars().all()
        assert all(len(token) == 64 for token in tokens)
        assert await connection.scalar(select(func.count()).select_from(SavedJob)) == 1


async def test_unsave_delete_account_and_rate_limit(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> None:
    config, job_id, _ = await setup(isolated_database, settings, record)
    engine = isolated_database[0]

    def exercise() -> None:
        with TestClient(
            create_app(config.model_copy(update={"auth_attempts_per_minute": 3}))
        ) as client:
            assert client.post("/auth/register", json=ACCOUNT).status_code == 201
            assert client.put(f"/me/saved-jobs/{job_id}").status_code == 204
            assert client.delete(f"/me/saved-jobs/{job_id}").status_code == 204
            assert client.get("/me/saved-jobs").json()["items"] == []
            assert client.put(f"/me/saved-jobs/{job_id}").status_code == 204
            assert client.delete("/auth/me").status_code == 204
            assert client.get("/auth/me").status_code == 401
            wrong = {**ACCOUNT, "password": "wrong password!"}
            assert client.post("/auth/login", json=wrong).status_code == 401
            assert client.post("/auth/login", json=wrong).status_code == 401
            limited = client.post("/auth/login", json=ACCOUNT)
            assert limited.status_code == 429 and limited.headers["retry-after"] == "60"

    await asyncio.to_thread(exercise)
    async with engine.connect() as connection:
        for table in (User, UserSession, SavedJob):
            assert await connection.scalar(select(func.count()).select_from(table)) == 0
