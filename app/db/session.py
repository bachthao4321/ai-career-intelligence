import math

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    options = make_url(settings.database_url.get_secret_value()).query.get("options", "")
    if not isinstance(options, str):
        raise ValueError("DATABASE_URL supports one options parameter")
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=settings.dependency_timeout_seconds,
        connect_args={
            "connect_timeout": max(1, math.ceil(settings.dependency_timeout_seconds)),
            "options": f"{options} -c statement_timeout={settings.db_statement_timeout_ms}".strip(),
        },
    )
