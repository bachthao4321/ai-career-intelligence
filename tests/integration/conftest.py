import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings
from tests.db_helpers import migrate


@pytest.fixture
async def isolated_database() -> AsyncIterator[tuple[AsyncEngine, str]]:
    url = Settings().database_url.get_secret_value()
    admin_engine = create_async_engine(url)
    schema = "test_career_" + uuid.uuid4().hex
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
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
