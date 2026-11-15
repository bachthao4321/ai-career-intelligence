"""Email/password accounts with server-side sessions, and per-user saved jobs."""

import asyncio
import hashlib
import hmac
import re
import secrets
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.api.jobs import JobSummary, get_session, permitted_jobs
from app.core.config import Settings
from app.db.models import Job, SavedJob, User, UserSession
from app.security.demo import client_address, reserve_client
from app.security.passwords import dummy_hash, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["Accounts"])
saved_router = APIRouter(prefix="/me/saved-jobs", tags=["Saved jobs"])

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(\.[^@\s.]+)+$")
MAX_SAVED_JOBS = 500
# scrypt uses 128 MiB per hash; bound concurrent hashing so a login burst cannot exhaust memory.
HASH_SLOTS = threading.BoundedSemaphore(2)
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL.fullmatch(value):
            raise ValueError("Invalid email address")
        return value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    created_at: datetime


class SavedJobOut(BaseModel):
    job: JobSummary
    saved_at: datetime


class SavedJobList(BaseModel):
    items: list[SavedJobOut]
    limit: int = MAX_SAVED_JOBS


def settings_of(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def cookie_name(settings: Settings) -> str:
    return "__Host-career_user" if settings.app_env == "production" else "career_user"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def same_origin_write(request: Request) -> None:
    """Cookie-authenticated writes must come from this site (SameSite=Strict is the first line)."""
    if request.method not in UNSAFE:
        return
    settings = settings_of(request)
    origin = request.headers.get("origin")
    if origin is not None:
        expected = settings.public_origin or str(request.base_url).rstrip("/")
        if origin.rstrip("/") != expected:
            raise HTTPException(403, "Same-origin request required")
    elif request.headers.get("sec-fetch-site") not in {None, "same-origin", "none"}:
        raise HTTPException(403, "Same-origin request required")


async def auth_rate_limit(request: Request) -> None:
    settings = settings_of(request)
    key = settings.demo_cookie_secret.get_secret_value() if settings.demo_cookie_secret else ""
    identity = hmac.new(
        (key or "career-auth").encode(),
        ("auth:" + client_address(request, settings)).encode(),
        hashlib.sha256,
    ).hexdigest()
    try:
        granted = await reserve_client(
            cast(AsyncEngine, request.app.state.engine),
            identity,
            "auth_minute",
            settings.auth_attempts_per_minute,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(503, "Account storage unavailable") from exc
    if not granted:
        raise HTTPException(429, "Too many attempts; retry later", headers={"Retry-After": "60"})


def _bounded_hash(password: str) -> str:
    with HASH_SLOTS:
        return hash_password(password)


def _bounded_verify(password: str, stored: str) -> bool:
    with HASH_SLOTS:
        return verify_password(password, stored)


async def start_session(
    session: AsyncSession, settings: Settings, user_id: uuid.UUID, response: Response
) -> None:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    await session.execute(delete(UserSession).where(UserSession.expires_at < now))
    session.add(
        UserSession(
            token_hash=token_hash(token),
            user_id=user_id,
            expires_at=now + timedelta(days=settings.user_session_days),
        )
    )
    await session.commit()
    response.set_cookie(
        cookie_name(settings),
        token,
        max_age=settings.user_session_days * 86400,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="strict",
        path="/",
    )


async def current_user(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> User:
    token = request.cookies.get(cookie_name(settings_of(request)), "")
    if not 32 <= len(token) <= 128:
        raise HTTPException(401, "Login required")
    user = await session.scalar(
        select(User)
        .join(UserSession, UserSession.user_id == User.id)
        .where(UserSession.token_hash == token_hash(token), UserSession.expires_at > func.now())
    )
    if user is None:
        raise HTTPException(401, "Login required")
    return user


@router.post(
    "/register",
    status_code=201,
    response_model=UserOut,
    dependencies=[Depends(same_origin_write), Depends(auth_rate_limit)],
)
async def register(
    body: Credentials,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserOut:
    password_hash = await asyncio.to_thread(_bounded_hash, body.password)
    user = User(email=body.email, password_hash=password_hash)
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "Email already registered") from exc
    await session.refresh(user)
    out = UserOut.model_validate(user)
    await start_session(session, settings_of(request), user.id, response)
    return out


@router.post(
    "/login",
    response_model=UserOut,
    dependencies=[Depends(same_origin_write), Depends(auth_rate_limit)],
)
async def login(
    body: Credentials,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserOut:
    user = await session.scalar(select(User).where(User.email == body.email))
    stored = user.password_hash if user else dummy_hash()
    valid = await asyncio.to_thread(_bounded_verify, body.password, stored)
    if user is None or not valid:
        raise HTTPException(401, "Invalid email or password")
    out = UserOut.model_validate(user)
    await start_session(session, settings_of(request), user.id, response)
    return out


@router.post("/logout", status_code=204, dependencies=[Depends(same_origin_write)])
async def logout(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> Response:
    settings = settings_of(request)
    token = request.cookies.get(cookie_name(settings), "")
    if token:
        hashed = token_hash(token)
        await session.execute(delete(UserSession).where(UserSession.token_hash == hashed))
        await session.commit()
    response = Response(status_code=204)
    response.delete_cookie(
        cookie_name(settings), path="/", secure=settings.app_env == "production", httponly=True
    )
    return response


@router.get("/me", response_model=UserOut)
async def me(user: Annotated[User, Depends(current_user)]) -> User:
    return user


@router.delete("/me", status_code=204, dependencies=[Depends(same_origin_write)])
async def delete_account(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    # Sessions and saved jobs are removed by ON DELETE CASCADE.
    await session.execute(delete(User).where(User.id == user.id))
    await session.commit()
    settings = settings_of(request)
    response = Response(status_code=204)
    response.delete_cookie(
        cookie_name(settings), path="/", secure=settings.app_env == "production", httponly=True
    )
    return response


@saved_router.get("", response_model=SavedJobList)
async def list_saved(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SavedJobList:
    rows = (
        await session.execute(
            select(Job, SavedJob.created_at)
            .join(SavedJob, SavedJob.job_id == Job.id)
            .where(SavedJob.user_id == user.id, permitted_jobs(request))
            .order_by(SavedJob.created_at.desc(), Job.id)
            .limit(MAX_SAVED_JOBS)
        )
    ).all()
    return SavedJobList(
        items=[SavedJobOut(job=JobSummary.model_validate(job), saved_at=at) for job, at in rows]
    )


@saved_router.put("/{job_id}", status_code=204, dependencies=[Depends(same_origin_write)])
async def save_job(
    request: Request,
    job_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    visible = select(Job.id).where(Job.id == job_id, permitted_jobs(request))
    if await session.scalar(visible) is None:
        raise HTTPException(404, "Job not found")
    already = await session.get(SavedJob, (user.id, job_id))
    count = await session.scalar(
        select(func.count()).select_from(SavedJob).where(SavedJob.user_id == user.id)
    )
    if already is None and (count or 0) >= MAX_SAVED_JOBS:
        raise HTTPException(409, "Saved job limit reached")
    await session.execute(
        insert(SavedJob).values(user_id=user.id, job_id=job_id).on_conflict_do_nothing()
    )
    await session.commit()
    return Response(status_code=204)


@saved_router.delete("/{job_id}", status_code=204, dependencies=[Depends(same_origin_write)])
async def unsave_job(
    job_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    await session.execute(
        delete(SavedJob).where(SavedJob.user_id == user.id, SavedJob.job_id == job_id)
    )
    await session.commit()
    return Response(status_code=204)
