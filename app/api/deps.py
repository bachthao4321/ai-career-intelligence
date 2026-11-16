"""FastAPI dependencies: the only place routers reach into app state."""

import hashlib
import hmac
from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.container import Services
from app.core.security.demo import client_address, client_hash
from app.db.models import User
from app.repositories.accounts import AccountRepository
from app.repositories.jobs import JobRepository
from app.repositories.quotas import reserve_client
from app.services.accounts import AccountService, session_cookie
from app.services.jobs import JobService

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_services(request: Request) -> Services:
    return cast(Services, request.app.state.services)


def get_engine(request: Request) -> AsyncEngine:
    return get_services(request).engine


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    try:
        async with async_sessionmaker(get_engine(request))() as session:
            yield session
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Job storage unavailable") from exc


SettingsDep = Annotated[Settings, Depends(get_settings)]
ServicesDep = Annotated[Services, Depends(get_services)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_job_repository(session: SessionDep, settings: SettingsDep) -> JobRepository:
    return JobRepository(session, settings.app_env)


def get_job_service(
    jobs: Annotated[JobRepository, Depends(get_job_repository)], settings: SettingsDep
) -> JobService:
    return JobService(jobs, settings)


def get_account_service(
    session: SessionDep,
    jobs: Annotated[JobRepository, Depends(get_job_repository)],
    settings: SettingsDep,
) -> AccountService:
    return AccountService(AccountRepository(session), jobs, settings)


JobServiceDep = Annotated[JobService, Depends(get_job_service)]
AccountServiceDep = Annotated[AccountService, Depends(get_account_service)]


async def current_user(
    request: Request, accounts: AccountServiceDep, settings: SettingsDep
) -> User:
    user = await accounts.user_for(request.cookies.get(session_cookie(settings), ""))
    if user is None:
        raise HTTPException(401, "Login required")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def same_origin_write(request: Request, settings: SettingsDep) -> None:
    """Cookie-authenticated writes must come from this site (SameSite=Strict is the first line)."""
    if request.method not in UNSAFE:
        return
    origin = request.headers.get("origin")
    if origin is not None:
        expected = settings.public_origin or str(request.base_url).rstrip("/")
        if origin.rstrip("/") != expected:
            raise HTTPException(403, "Same-origin request required")
    elif request.headers.get("sec-fetch-site") not in {None, "same-origin", "none"}:
        raise HTTPException(403, "Same-origin request required")


async def auth_rate_limit(request: Request, settings: SettingsDep) -> None:
    key = settings.demo_cookie_secret.get_secret_value() if settings.demo_cookie_secret else ""
    identity = hmac.new(
        (key or "career-auth").encode(),
        ("auth:" + client_address(request, settings)).encode(),
        hashlib.sha256,
    ).hexdigest()
    try:
        granted = await reserve_client(
            get_engine(request), identity, "auth_minute", settings.auth_attempts_per_minute
        )
    except SQLAlchemyError as exc:
        raise HTTPException(503, "Account storage unavailable") from exc
    if not granted:
        raise HTTPException(429, "Too many attempts; retry later", headers={"Retry-After": "60"})


async def paid_client_gate(request: Request, enabled: bool) -> None:
    """Per-client daily cap on routes that may call a paid model."""
    settings = get_settings(request)
    if not enabled or not settings.client_limits_enabled:
        return
    try:
        granted = await reserve_client(
            get_engine(request),
            client_hash(request, settings),
            "llm_day",
            settings.client_llm_routes_per_day,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(503, "Client quota storage unavailable") from exc
    if not granted:
        raise HTTPException(
            429, "Daily model-route limit reached", headers={"Retry-After": "86400"}
        )


def require_admin(request: Request, settings: SettingsDep) -> None:
    key = settings.admin_key
    supplied = request.headers.get("x-admin-key", "")
    if (
        not key
        or len(supplied) > 256
        or not hmac.compare_digest(supplied.encode(), key.get_secret_value().encode())
    ):
        raise HTTPException(403, "Admin key required")
