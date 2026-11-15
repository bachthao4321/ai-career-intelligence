from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import ClientQuota, LlmDailyUsage

WINDOWS = {"minute": timedelta(minutes=1), "auth_minute": timedelta(minutes=1)}


async def reserve_client(engine: AsyncEngine, identity: str, category: str, limit: int) -> bool:
    """Atomically count one request in the client's current window; False once `limit` is hit."""
    if limit <= 0:
        return False
    if category not in {"minute", "llm_day", "auth_minute"}:
        raise ValueError("Unknown client quota category")
    async with engine.begin() as connection:
        now = (await connection.execute(select(func.clock_timestamp()))).scalar_one()
        if category == "llm_day":
            # UTC regardless of the database/session timezone.
            window = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            duration = timedelta(days=1)
        else:
            window = now.replace(second=0, microsecond=0)
            duration = WINDOWS[category]
        await connection.execute(delete(ClientQuota).where(ClientQuota.expires_at < now))
        statement = insert(ClientQuota).values(
            client_hash=identity,
            category=category,
            window_start=window,
            used=1,
            expires_at=window + duration,
        )
        granted = await connection.scalar(
            statement.on_conflict_do_update(
                index_elements=[
                    ClientQuota.client_hash,
                    ClientQuota.category,
                    ClientQuota.window_start,
                ],
                set_={"used": ClientQuota.used + 1},
                where=ClientQuota.used < limit,
            ).returning(ClientQuota.used)
        )
        return granted is not None


async def reserve_daily_call(engine: AsyncEngine, limit: int) -> bool:
    """Global per-UTC-day model call budget; reservations are never refunded."""
    if limit <= 0:
        return False
    statement = insert(LlmDailyUsage).values(day=datetime.now(UTC).date(), calls=1)
    reservation = statement.on_conflict_do_update(
        index_elements=[LlmDailyUsage.day],
        set_={"calls": LlmDailyUsage.calls + 1},
        where=LlmDailyUsage.calls < limit,
    ).returning(LlmDailyUsage.calls)
    # Separate committed transaction: a failed import must not refund an API attempt.
    async with engine.begin() as connection:
        return await connection.scalar(reservation) is not None
