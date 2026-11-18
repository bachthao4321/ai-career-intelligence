import asyncio
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.models import Job, SavedJob, User, UserSession
from app.ingestion.service import import_records
from app.main import create_app
from app.schemas.ingestion import JobRecord
from tests.db_helpers import migrate

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Set RUN_INTEGRATION=1"),
]

ACCOUNT = {"email": "Ana@Example.com", "username": "Ana", "password": "correct horse battery"}
LOGIN = {"login": "ana@example.com", "password": ACCOUNT["password"]}


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
        relaxed = config.model_copy(update={"auth_attempts_per_minute": 30})
        with TestClient(create_app(relaxed)) as client:
            assert client.get("/auth/me").status_code == 401
            assert client.get("/me/saved-jobs").status_code == 401
            assert client.put(f"/me/saved-jobs/{job_id}").status_code == 401
            bad = {"email": "not-an-email", "username": "aaa", "password": "long enough"}
            assert client.post("/auth/register", json=bad).status_code == 422
            short = {"email": "a@example.com", "username": "aaa", "password": "short"}
            assert client.post("/auth/register", json=short).status_code == 422

            created = client.post("/auth/register", json=ACCOUNT)
            assert created.status_code == 201, created.text
            assert created.json()["email"] == "ana@example.com"
            assert created.json()["username"] == "ana"
            assert "password" not in created.text
            assert created.headers["cache-control"] == "no-store"
            cookie = created.headers["set-cookie"]
            assert "HttpOnly" in cookie and "SameSite=strict" in cookie
            assert client.get("/auth/me").json()["email"] == "ana@example.com"
            again = {**ACCOUNT, "email": "ANA@example.com", "username": "other"}
            taken = client.post("/auth/register", json=again)
            assert taken.status_code == 409 and taken.json()["detail"] == "Email already registered"
            same_name = {**ACCOUNT, "email": "new@example.com", "username": "ANA"}
            taken = client.post("/auth/register", json=same_name)
            assert taken.status_code == 409 and taken.json()["detail"] == "Username already taken"
            bad_name = {**ACCOUNT, "email": "new@example.com", "username": "a b"}
            assert client.post("/auth/register", json=bad_name).status_code == 422

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
            assert saved[0]["status"] == "saved"

            status_url = f"/me/saved-jobs/{job_id}"
            assert client.patch(status_url, json={"status": "applied"}).status_code == 204
            assert client.get("/me/saved-jobs").json()["items"][0]["status"] == "applied"
            # Saving again is idempotent and keeps the pipeline position.
            assert client.put(status_url).status_code == 204
            assert client.get("/me/saved-jobs").json()["items"][0]["status"] == "applied"
            assert client.patch(status_url, json={"status": "hired"}).status_code == 422
            assert client.patch(status_url, json={"status": "offer", "x": 1}).status_code == 422
            unknown_job = f"/me/saved-jobs/{uuid.uuid4()}"
            assert client.patch(unknown_job, json={"status": "offer"}).status_code == 404
            assert (
                client.patch(status_url, json={"status": "offer"}, headers=evil).status_code == 403
            )

            assert client.post("/auth/logout").status_code == 204
            assert client.get("/auth/me").status_code == 401
            wrong = {**LOGIN, "password": "wrong password!"}
            assert client.post("/auth/login", json=wrong).status_code == 401
            unknown = {**LOGIN, "login": "nobody@example.com"}
            missing = client.post("/auth/login", json=unknown)
            assert missing.status_code == 401
            assert missing.json() == client.post("/auth/login", json=wrong).json()
            assert client.post("/auth/login", json=LOGIN).status_code == 200
            assert len(client.get("/me/saved-jobs").json()["items"]) == 1
            by_name = client.post("/auth/login", json={**LOGIN, "login": "ANA"})
            assert by_name.status_code == 200 and by_name.json()["username"] == "ana"
            assert client.post("/auth/login", json=ACCOUNT).status_code == 422

        with TestClient(create_app(relaxed)) as other:
            other_account = {"email": "bo@example.com", "username": "bo.tran", "password": "x" * 12}
            assert other.post("/auth/register", json=other_account).status_code == 201
            assert other.get("/me/saved-jobs").json()["items"] == []
            # Another user's saved job is not theirs to move.
            assert (
                other.patch(f"/me/saved-jobs/{job_id}", json={"status": "rejected"}).status_code
                == 404
            )
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
        assert await connection.scalar(select(SavedJob.status)) == "applied"


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
            wrong = {**LOGIN, "password": "wrong password!"}
            assert client.post("/auth/login", json=wrong).status_code == 401
            assert client.post("/auth/login", json=wrong).status_code == 401
            limited = client.post("/auth/login", json=LOGIN)
            assert limited.status_code == 429 and limited.headers["retry-after"] == "60"

    await asyncio.to_thread(exercise)
    async with engine.connect() as connection:
        for table in (User, UserSession, SavedJob):
            assert await connection.scalar(select(func.count()).select_from(table)) == 0


async def test_migration_backfills_valid_unique_usernames(
    isolated_database: tuple[AsyncEngine, str],
) -> None:
    engine, url = isolated_database
    await asyncio.to_thread(migrate, url, "downgrade", "0012_application_status")
    emails = ["ana.nguyen@a.com", "Ana.Nguyen@b.com", "x@c.com", "._weird-name+tag@d.com"]
    async with engine.begin() as connection:
        for email in emails:
            await connection.execute(
                text("INSERT INTO users (email, password_hash) VALUES (:email, 'scrypt$x')"),
                {"email": email.lower()},
            )
    await asyncio.to_thread(migrate, url, "upgrade", "head")
    async with engine.connect() as connection:
        names = dict(
            (await connection.execute(text("SELECT email, username FROM users"))).tuples().all()
        )
    # Same stem: exactly one keeps it, the other gets an id-derived suffix.
    pair = sorted([names["ana.nguyen@a.com"], names["ana.nguyen@b.com"]])
    assert pair[0] == "ana.nguyen" and pair[1].startswith("ana.nguyen_")
    assert names["x@c.com"] == "user"
    assert names["._weird-name+tag@d.com"] == "weirdnametag"
    assert len(set(names.values())) == len(names)
