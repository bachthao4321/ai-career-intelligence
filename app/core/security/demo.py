"""Shared demo gate plus independent CV capabilities and operator keys."""

import base64
import hashlib
import hmac
import ipaddress
import secrets
import time
from datetime import timedelta
from typing import cast
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.models import ClientQuota


def cookie_name(settings: Settings) -> str:
    return "__Host-career_demo" if settings.app_env == "production" else "career_demo"


def signature(settings: Settings, value: str) -> str:
    assert settings.demo_cookie_secret is not None
    # Password rotation also invalidates sessions; no passwords are stored in cookies.
    key = settings.demo_cookie_secret.get_secret_value().encode()
    password = settings.demo_password.get_secret_value() if settings.demo_password else ""
    return hmac.new(key, ("session:" + password + ":" + value).encode(), hashlib.sha256).hexdigest()


def issue_session(settings: Settings) -> str:
    value = f"v1.{int(time.time()) + settings.demo_session_seconds}.{secrets.token_urlsafe(18)}"
    return value + "." + signature(settings, value)


def valid_session(settings: Settings, token: str) -> bool:
    if len(token) > 256:
        return False
    try:
        version, expiry, nonce, mac = token.split(".")
        valid_time = (
            int(time.time()) < int(expiry) <= int(time.time()) + settings.demo_session_seconds
        )
        return (
            version == "v1"
            and len(nonce) == 24
            and valid_time
            and hmac.compare_digest(mac, signature(settings, f"{version}.{expiry}.{nonce}"))
        )
    except (ValueError, TypeError):
        return False


def valid_basic(settings: Settings, authorization: str) -> bool:
    if len(authorization) > 1024 or not authorization.lower().startswith("basic "):
        return False
    try:
        raw = base64.b64decode(authorization.split(" ", 1)[1], validate=True).decode("utf-8")
        username, password = raw.split(":", 1)
    except (ValueError, UnicodeError):
        return False
    assert settings.demo_password is not None
    return hmac.compare_digest(
        username.encode(), settings.demo_username.encode()
    ) & hmac.compare_digest(password.encode(), settings.demo_password.get_secret_value().encode())


def client_address(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client else "unknown"
    address = peer
    if settings.trusted_proxy_ip and peer == settings.trusted_proxy_ip:
        try:
            address = str(ipaddress.ip_address(request.headers.get("x-forwarded-for", "")))
        except ValueError:
            pass  # Invalid/chained headers collapse to the proxy bucket, never a fresh identity.
    return address


def client_hash(request: Request, settings: Settings) -> str:
    assert settings.demo_cookie_secret is not None
    return hmac.new(
        settings.demo_cookie_secret.get_secret_value().encode(),
        ("client:" + client_address(request, settings)).encode(),
        hashlib.sha256,
    ).hexdigest()


async def reserve_client(engine: AsyncEngine, identity: str, category: str, limit: int) -> bool:
    if limit <= 0:
        return False
    if category not in {"minute", "llm_day", "auth_minute"}:
        raise ValueError("Unknown client quota category")
    async with engine.begin() as connection:
        now = (await connection.execute(select(func.clock_timestamp()))).scalar_one()
        window = now.replace(second=0, microsecond=0)
        duration = timedelta(minutes=1)
        if category == "llm_day":
            # UTC regardless of the database/session timezone.
            from datetime import UTC

            window = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            duration = timedelta(days=1)
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


async def paid_client_gate(request: Request, enabled: bool) -> None:
    settings = cast(Settings, request.app.state.settings)
    if not enabled or not settings.client_limits_enabled:
        return
    try:
        granted = await reserve_client(
            request.app.state.engine,
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


def require_admin(request: Request) -> None:
    key = cast(Settings, request.app.state.settings).admin_key
    supplied = request.headers.get("x-admin-key", "")
    if (
        not key
        or len(supplied) > 256
        or not hmac.compare_digest(supplied.encode(), key.get_secret_value().encode())
    ):
        raise HTTPException(403, "Admin key required")


def same_origin(request: Request, settings: Settings) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        # Browser cookies alone must not authorize an unsafe request without Origin.
        return (
            cookie_name(settings) not in request.cookies
            or request.headers.get("sec-fetch-site") == "same-origin"
            or valid_basic(settings, request.headers.get("authorization", ""))
        )
    expected = settings.public_origin or str(request.base_url).rstrip("/")
    return origin.rstrip("/") == expected


def host_allowed(request: Request, settings: Settings) -> bool:
    if settings.app_env != "production":
        return True
    allowed = {urlsplit(settings.public_origin or "").hostname, "localhost", "127.0.0.1"}
    return request.url.hostname in allowed
