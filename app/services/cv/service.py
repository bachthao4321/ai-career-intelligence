import asyncio
import hashlib
import logging
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.db.models import CVDocument
from app.schemas.cv import CVProfile, CVUploaded, CVView, MatchReport, MatchRequest
from app.services.cv.matching import match_jobs
from app.services.cv.parser import parse_pdf
from app.services.search.documents import load_documents


class CVNotFound(LookupError):
    pass


class CVCapacityReached(RuntimeError):
    pass


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class CVService:
    def __init__(self, engine: AsyncEngine, settings: Settings):
        self.engine, self.settings = engine, settings
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.upload_slots = asyncio.Semaphore(2)
        self.match_slots = asyncio.Semaphore(2)

    async def upload(self, data: bytes) -> CVUploaded:
        profile = await parse_pdf(data)
        token = secrets.token_urlsafe(32)
        async with self.sessions.begin() as session:
            # Serialize capacity admission across workers; expired rows do not consume capacity.
            await session.execute(text("SELECT pg_advisory_xact_lock(44551182001)"))
            await session.execute(delete(CVDocument).where(CVDocument.expires_at <= func.now()))
            count = await session.scalar(select(func.count()).select_from(CVDocument))
            if (count or 0) >= self.settings.cv_capacity:
                raise CVCapacityReached("cv_storage_capacity_reached")
            document = CVDocument(
                id=uuid.uuid4(),
                token_hash=token_hash(token),
                profile=profile.model_dump(mode="json"),
                expires_at=func.now() + timedelta(seconds=self.settings.cv_ttl_seconds),
            )
            session.add(document)
            await session.flush()
            await session.refresh(document)
            result = CVUploaded(
                id=document.id,
                created_at=document.created_at,
                expires_at=document.expires_at,
                profile=profile,
                access_token=token,
            )
        return result

    @asynccontextmanager
    async def access(self, cv_id: uuid.UUID, token: str) -> AsyncIterator[CVView]:
        async with self.sessions.begin() as session:
            document = await session.scalar(
                select(CVDocument)
                .where(
                    CVDocument.id == cv_id,
                    CVDocument.token_hash == token_hash(token),
                    CVDocument.expires_at > func.clock_timestamp(),
                )
                .with_for_update(read=True)
            )
            if document is None:
                raise CVNotFound()
            view = CVView(
                id=document.id,
                created_at=document.created_at,
                expires_at=document.expires_at,
                profile=CVProfile.model_validate(document.profile),
            )
            yield view
            # A long match must not return a profile/report after its TTL passed.
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            if now >= document.expires_at:
                raise CVNotFound()

    async def get(self, cv_id: uuid.UUID, token: str) -> CVView:
        async with self.access(cv_id, token) as view:
            return view

    async def delete(self, cv_id: uuid.UUID, token: str) -> None:
        async with self.sessions.begin() as session:
            deleted = await session.scalar(
                delete(CVDocument)
                .where(CVDocument.id == cv_id, CVDocument.token_hash == token_hash(token))
                .returning(CVDocument.id)
            )
            if deleted is None:
                raise CVNotFound()

    async def match(self, cv_id: uuid.UUID, token: str, request: MatchRequest) -> MatchReport:
        try:
            await asyncio.wait_for(self.match_slots.acquire(), timeout=0.1)
        except TimeoutError as exc:
            raise CVCapacityReached("cv_matching_busy") from exc
        try:
            async with self.access(cv_id, token) as cv:
                docs = await load_documents(self.engine, self.settings, request.filters)
                return await asyncio.to_thread(
                    match_jobs, cv.id, cv.expires_at, cv.profile, request, docs
                )
        finally:
            self.match_slots.release()

    async def cleanup(self) -> int:
        async with self.sessions.begin() as session:
            removed = (
                await session.scalars(
                    delete(CVDocument)
                    .where(CVDocument.expires_at <= func.clock_timestamp())
                    .returning(CVDocument.id)
                )
            ).all()
            return len(removed)

    async def cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.cv_cleanup_interval_seconds)
            try:
                await self.cleanup()
            except SQLAlchemyError:
                logging.getLogger(__name__).warning("cv_expiry_cleanup_failed")
