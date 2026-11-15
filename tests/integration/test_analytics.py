import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import HttpUrl, SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.security.sql import UnsafeSQL
from app.ingestion.service import import_records
from app.main import create_app
from app.schemas.ingestion import JobRecord
from app.services.ask.analytics import AnalyticsExecutor, AnalyticsUnavailable, provision_role
from app.services.ask.planner import ModelPlanner, Plan, deterministic_plan
from app.services.ask.workflow import AskRequest, CareerAgent

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="Set RUN_INTEGRATION=1"),
]


@dataclass
class AnalyticsHarness:
    engine: AsyncEngine
    local: AnalyticsExecutor
    public: AnalyticsExecutor
    local_settings: Settings
    public_settings: Settings


@pytest.fixture
async def analytics(
    isolated_database: tuple[AsyncEngine, str], settings: Settings, record: JobRecord
) -> AsyncIterator[AnalyticsHarness]:
    engine, admin_url = isolated_database
    schema = make_url(admin_url).query["options"].split("=")[1]
    roles = []
    executors = []
    configs = []
    try:
        for scope in ("local", "public"):
            role = "test_analytics_" + uuid.uuid4().hex
            roles.append(role)
            password = uuid.uuid4().hex
            await asyncio.to_thread(provision_role, admin_url, schema, role, password, scope)
            dsn = (
                make_url(admin_url)
                .set(username=role, password=password)
                .difference_update_query(["options"])
                .render_as_string(hide_password=False)
            )
            config = settings.model_copy(
                update={
                    "database_url": SecretStr(admin_url),
                    "analytics_database_url": SecretStr(dsn),
                    "analytics_schema": schema,
                    "app_env": "local" if scope == "local" else "test",
                    "analytics_timeout_ms": 100,
                }
            )
            configs.append(config)
            executors.append(AnalyticsExecutor(config))
        public_record = record.model_copy(update={"source": "jobtech"})
        duplicate = public_record.model_copy(update={"source_job_id": "duplicate"})
        private = record.model_copy(update={"source": "itviec", "source_job_id": "private"})
        renamed = record.model_copy(
            update={
                "source": "renamed",
                "source_job_id": "renamed",
                "source_url": HttpUrl("https://www.topcv.vn/viec-lam/123"),
            }
        )
        await import_records(engine, [public_record, duplicate, private, renamed], configs[0])
        yield AnalyticsHarness(engine, executors[0], executors[1], configs[0], configs[1])
    finally:
        for executor in executors:
            await executor.close()
        async with engine.begin() as conn:
            for role in roles:
                # Only this fixture's random role and its grants are removed.
                await conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
                await conn.exec_driver_sql(f'DROP ROLE "{role}"')


async def test_scoped_counts_duplicates_and_current_evidence(analytics: AnalyticsHarness) -> None:
    a = analytics
    query = "SELECT COUNT(*) AS n, COUNT(DISTINCT content_hash) AS unique_n FROM jobs"
    assert (await a.local.execute(query)).rows == [{"n": 4, "unique_n": 1}]
    assert (await a.public.execute(query)).rows == [{"n": 2, "unique_n": 1}]
    query = "SELECT COUNT(DISTINCT job_id) AS n FROM job_skills WHERE skill_slug='python'"
    assert (await a.public.execute(query)).rows == [{"n": 2}]
    async with a.engine.begin() as conn:
        await conn.execute(
            text("UPDATE jobs SET content_hash=:hash WHERE source_job_id='duplicate'"),
            {"hash": "a" * 64},
        )
    assert (await a.public.execute(query)).rows == [{"n": 1}]


@pytest.mark.parametrize(
    "operator,pattern,expected",
    [("LIKE", "%Minh%", 2), ("ILIKE", "%MINH%", 2), ("LIKE", "%s", 0)],
)
async def test_location_patterns_are_literals_not_driver_parameters(
    analytics: AnalyticsHarness, operator: str, pattern: str, expected: int
) -> None:
    query = f"SELECT COUNT(*) AS n FROM jobs WHERE location {operator} '{pattern}'"
    assert (await analytics.public.execute(query)).rows == [{"n": expected}]


async def test_database_denies_base_access_writes_and_cross_scope_even_without_validator(
    analytics: AnalyticsHarness,
) -> None:
    for executor in (analytics.local, analytics.public):
        assert executor.engine
        schema = executor.settings.analytics_schema
        attempts = [
            f'SELECT * FROM "{schema}".jobs',
            f"UPDATE \"{schema}\".jobs SET title='changed'",
            f"INSERT INTO \"{schema}\".analytics_{executor.scope}_jobs (title) VALUES ('x')",
            f'DELETE FROM "{schema}".analytics_{executor.scope}_jobs',
            f'CREATE TABLE "{schema}".bad (id integer)',
        ]
        if executor.scope == "public":
            attempts.append(f'SELECT * FROM "{schema}".analytics_local_jobs')
        for query in attempts:
            async with executor.engine.connect() as conn:
                # Deliberately disable read-only transaction mode: privileges must still deny it.
                await conn.exec_driver_sql("SET TRANSACTION READ WRITE")
                with pytest.raises(DBAPIError):
                    await conn.exec_driver_sql(query)
                await conn.rollback()


async def test_db_timeout_without_ast_and_connection_recovery(analytics: AnalyticsHarness) -> None:
    executor = analytics.public
    assert executor.engine
    async with executor.engine.connect() as conn:
        assert await conn.scalar(text("SHOW transaction_read_only")) == "on"
        await conn.exec_driver_sql("SET LOCAL statement_timeout=100")
        with pytest.raises(DBAPIError):
            await conn.exec_driver_sql("SELECT pg_sleep(1)")
        await conn.rollback()
    assert (await executor.execute("SELECT COUNT(*) AS n FROM jobs")).rows == [{"n": 2}]


async def test_executor_rejects_admin_and_generated_dangerous_sql(
    analytics: AnalyticsHarness,
) -> None:
    config = analytics.public_settings.model_copy(
        update={"analytics_database_url": analytics.public_settings.database_url}
    )
    executor = AnalyticsExecutor(config)
    try:
        with pytest.raises(AnalyticsUnavailable, match="restricted"):
            await executor.execute("SELECT COUNT(*) FROM jobs")
    finally:
        await executor.close()
    with pytest.raises(UnsafeSQL):
        await analytics.public.execute("SELECT pg_sleep(1) FROM jobs")


async def test_five_offline_sql_answers_and_graph(analytics: AnalyticsHarness) -> None:
    expected = [
        ("Có bao nhiêu tin?", [{"listing_count": 2, "unique_content_count": 1}]),
        ("Có bao nhiêu tin nhắc đến Python?", [{"listing_count": 2}]),
        ("Có bao nhiêu tin nhắc đến Java?", [{"listing_count": 0}]),
        ("Nguồn nào có nhiều tin nhất?", [{"source": "jobtech", "listing_count": 2}]),
    ]
    for question, rows in expected:
        plan = deterministic_plan(question)
        assert plan and plan.sql
        assert (await analytics.public.execute(plan.sql)).rows == rows
    plan = deterministic_plan("Top 10 skills")
    assert plan and plan.sql
    top = await analytics.public.execute(plan.sql)
    assert any(row == {"skill_slug": "python", "listing_count": 2} for row in top.rows)
    planner = AsyncMock()
    planner.plan.return_value = Plan(route="analytics", sql="SELECT pg_sleep(10) FROM jobs")
    agent = CareerAgent(AsyncMock(), analytics.public, planner)
    response = await agent.ask(AskRequest(question="How many jobs?"))
    assert response.status == "ok" and response.planner == "deterministic"
    planner.plan.assert_not_called()
    response = await agent.ask(AskRequest(question="Unrecognized statistics"))
    assert response.status == "rejected"
    planner.plan.return_value = Plan(route="analytics", sql="SELECT COUNT(*) AS n FROM jobs")
    response = await agent.ask(AskRequest(question="An unfamiliar count question"))
    assert response.status == "ok" and response.planner == "model"
    assert response.analytics and response.analytics["rows"] == [{"n": 2}]


async def test_ask_api_uses_real_scoped_analytics_and_search(analytics: AnalyticsHarness) -> None:
    def exercise() -> None:
        with TestClient(create_app(analytics.public_settings)) as client:
            response = client.post("/ask", json={"question": "How many jobs?"})
            assert response.status_code == 200
            body = response.json()
            assert body["route"] == "analytics" and body["status"] == "ok"
            assert body["analytics"]["rows"] == [{"listing_count": 2, "unique_content_count": 1}]
            assert body["scope"] == "public_sources"
            body = client.post("/ask", json={"question": "Tìm việc Python"}).json()
            assert body["route"] == "search" and body["status"] == "ok"
            assert body["search"]["eligible_listings"] == 2
            assert all(item["source"] == "jobtech" for item in body["search"]["items"])

    await asyncio.to_thread(exercise)


def test_ask_api_without_model_or_analytics_credentials(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        result = client.post("/ask", json={"question": "How many jobs?"}).json()
        assert result["status"] == "unavailable"
        assert result["warnings"] == ["analytics_not_configured"]
        response = client.post("/ask", json={"question": "average salary?"})
        assert response.status_code == 200
        assert response.json()["status"] == "needs_clarification"
        assert client.post("/ask", json={"question": "x"}).status_code == 422
        assert (
            client.post("/ask", json={"question": "Python", "sql": "DROP TABLE jobs"}).status_code
            == 422
        )


async def test_real_model_budget_reservation_is_atomic(analytics: AnalyticsHarness) -> None:
    config = analytics.public_settings.model_copy(
        update={"agent_model_enabled": True, "agent_daily_call_limit": 1}
    )
    import httpx

    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(503)

    planner = ModelPlanner(config, analytics.engine, transport=httpx.MockTransport(handler))
    results = await asyncio.gather(*(planner.plan("unknown") for _ in range(5)))
    assert len(attempts) == 1
    assert sum(r.reason == "model_daily_budget_exhausted" for r in results) == 4
