"""Email/password accounts with server-side sessions, and per-user saved jobs."""

import asyncio
import hashlib
import secrets
import threading
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.security.passwords import dummy_hash, hash_password, verify_password
from app.db.models import User
from app.repositories.accounts import AccountRepository
from app.repositories.jobs import JobRepository
from app.schemas.accounts import (
    MAX_SAVED_JOBS,
    Credentials,
    SavedJobList,
    SavedJobOut,
    UserOut,
)
from app.schemas.jobs import JobSummary

# scrypt uses 128 MiB per hash; bound concurrent hashing so a login burst cannot exhaust memory.
HASH_SLOTS = threading.BoundedSemaphore(2)


class EmailTaken(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class JobNotFound(Exception):
    pass


class SavedLimitReached(Exception):
    pass


def session_cookie(settings: Settings) -> str:
    return "__Host-career_user" if settings.app_env == "production" else "career_user"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _bounded_hash(password: str) -> str:
    with HASH_SLOTS:
        return hash_password(password)


def _bounded_verify(password: str, stored: str) -> bool:
    with HASH_SLOTS:
        return verify_password(password, stored)


class AccountService:
    def __init__(self, accounts: AccountRepository, jobs: JobRepository, settings: Settings):
        self.accounts = accounts
        self.jobs = jobs
        self.settings = settings

    async def register(self, credentials: Credentials) -> tuple[UserOut, str]:
        password_hash = await asyncio.to_thread(_bounded_hash, credentials.password)
        try:
            user = await self.accounts.add_user(credentials.email, password_hash)
        except IntegrityError as exc:
            raise EmailTaken from exc
        return await self._signed_in(user)

    async def login(self, credentials: Credentials) -> tuple[UserOut, str]:
        user = await self.accounts.user_by_email(credentials.email)
        # Verify against a dummy hash for unknown emails so timing does not reveal accounts.
        stored = user.password_hash if user else dummy_hash()
        valid = await asyncio.to_thread(_bounded_verify, credentials.password, stored)
        if user is None or not valid:
            raise InvalidCredentials
        return await self._signed_in(user)

    async def _signed_in(self, user: User) -> tuple[UserOut, str]:
        # Snapshot the user before commit expires its loaded attributes.
        out = UserOut.model_validate(user)
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        expires = now + timedelta(days=self.settings.user_session_days)
        await self.accounts.add_session(user.id, token_hash(token), now, expires)
        await self.accounts.commit()
        return out, token

    async def user_for(self, token: str) -> User | None:
        if not 32 <= len(token) <= 128:
            return None
        return await self.accounts.user_by_session(token_hash(token))

    async def logout(self, token: str) -> None:
        if token:
            await self.accounts.delete_session(token_hash(token))
            await self.accounts.commit()

    async def delete(self, user: User) -> None:
        await self.accounts.delete_user(user.id)
        await self.accounts.commit()

    async def saved_jobs(self, user: User) -> SavedJobList:
        rows = await self.accounts.saved_jobs(user.id, self.jobs.visible, MAX_SAVED_JOBS)
        return SavedJobList(
            items=[SavedJobOut(job=JobSummary.model_validate(job), saved_at=at) for job, at in rows]
        )

    async def save_job(self, user: User, job_id: uuid.UUID) -> None:
        if await self.jobs.get(job_id) is None:
            raise JobNotFound
        already = await self.accounts.is_saved(user.id, job_id)
        if not already and await self.accounts.saved_count(user.id) >= MAX_SAVED_JOBS:
            raise SavedLimitReached
        await self.accounts.save(user.id, job_id)
        await self.accounts.commit()

    async def unsave_job(self, user: User, job_id: uuid.UUID) -> None:
        await self.accounts.unsave(user.id, job_id)
        await self.accounts.commit()
