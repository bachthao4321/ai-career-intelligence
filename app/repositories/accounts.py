import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import Job, SavedJob, User, UserSession


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_user(self, email: str, password_hash: str) -> User:
        """Flushes so a duplicate email surfaces as IntegrityError here."""
        user = User(email=email, password_hash=password_hash)
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def user_by_email(self, email: str) -> User | None:
        user: User | None = await self.session.scalar(select(User).where(User.email == email))
        return user

    async def user_by_session(self, token_hash: str) -> User | None:
        user: User | None = await self.session.scalar(
            select(User)
            .join(UserSession, UserSession.user_id == User.id)
            .where(UserSession.token_hash == token_hash, UserSession.expires_at > func.now())
        )
        return user

    async def delete_user(self, user_id: uuid.UUID) -> None:
        # Sessions and saved jobs are removed by ON DELETE CASCADE.
        await self.session.execute(delete(User).where(User.id == user_id))

    async def add_session(
        self, user_id: uuid.UUID, token_hash: str, now: datetime, expires_at: datetime
    ) -> None:
        await self.session.execute(delete(UserSession).where(UserSession.expires_at < now))
        self.session.add(UserSession(token_hash=token_hash, user_id=user_id, expires_at=expires_at))

    async def delete_session(self, token_hash: str) -> None:
        await self.session.execute(delete(UserSession).where(UserSession.token_hash == token_hash))

    async def saved_jobs(
        self, user_id: uuid.UUID, visible: ColumnElement[bool], limit: int
    ) -> list[tuple[Job, SavedJob]]:
        rows = await self.session.execute(
            select(Job, SavedJob)
            .join(SavedJob, SavedJob.job_id == Job.id)
            .where(SavedJob.user_id == user_id, visible)
            .order_by(SavedJob.created_at.desc(), Job.id)
            .limit(limit)
        )
        return [(job, saved) for job, saved in rows.all()]

    async def is_saved(self, user_id: uuid.UUID, job_id: uuid.UUID) -> bool:
        return await self.session.get(SavedJob, (user_id, job_id)) is not None

    async def saved_count(self, user_id: uuid.UUID) -> int:
        count = await self.session.scalar(
            select(func.count()).select_from(SavedJob).where(SavedJob.user_id == user_id)
        )
        return count or 0

    async def save(self, user_id: uuid.UUID, job_id: uuid.UUID) -> None:
        await self.session.execute(
            insert(SavedJob).values(user_id=user_id, job_id=job_id).on_conflict_do_nothing()
        )

    async def unsave(self, user_id: uuid.UUID, job_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(SavedJob).where(SavedJob.user_id == user_id, SavedJob.job_id == job_id)
        )

    async def set_status(self, user_id: uuid.UUID, job_id: uuid.UUID, status: str) -> bool:
        """Returns False when the job is not in the user's saved list."""
        result = await self.session.execute(
            update(SavedJob)
            .where(SavedJob.user_id == user_id, SavedJob.job_id == job_id)
            .values(status=status, status_updated_at=func.now())
            .returning(SavedJob.job_id)
        )
        return result.first() is not None

    async def commit(self) -> None:
        await self.session.commit()
