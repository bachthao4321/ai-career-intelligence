"""Request gate applied to every route: host check, client rate limit, demo login, headers."""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.security.demo import (
    client_hash,
    cookie_name,
    host_allowed,
    issue_session,
    same_origin,
    valid_basic,
    valid_session,
)
from app.repositories.quotas import reserve_client

PROBES = {"/health", "/ready"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PRIVATE_PREFIXES = ("/cv", "/auth/", "/me/")

Next = Callable[[Request], Awaitable[Response]]


def rejected(code: int, detail: str, headers: dict[str, str] | None = None) -> Response:
    return JSONResponse(
        {"detail": detail},
        status_code=code,
        headers={"Cache-Control": "no-store", **(headers or {})},
    )


def install_security(app: FastAPI, config: Settings) -> None:
    @app.middleware("http")
    async def security_gate(request: Request, call_next: Next) -> Response:
        if not host_allowed(request, config):
            return rejected(400, "Unrecognized host")
        if request.url.path in PROBES and request.method in {"GET", "HEAD"}:
            response = await call_next(request)
        else:
            response = await gated(request, call_next, config)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith(PRIVATE_PREFIXES):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response


async def gated(request: Request, call_next: Next, config: Settings) -> Response:
    if config.client_limits_enabled:
        try:
            granted = await reserve_client(
                engine=request.app.state.services.engine,
                identity=client_hash(request, config),
                category="minute",
                limit=config.client_requests_per_minute,
            )
        except SQLAlchemyError:
            return rejected(503, "Client quota storage unavailable")
        if not granted:
            return rejected(429, "Request rate limit reached", {"Retry-After": "60"})
    if not config.demo_auth_enabled:
        return await call_next(request)

    authenticated = valid_session(config, request.cookies.get(cookie_name(config), ""))
    login = valid_basic(config, request.headers.get("authorization", ""))
    if not authenticated and not login:
        return rejected(
            401,
            "Demo login required",
            {"WWW-Authenticate": 'Basic realm="CareerLens", charset="UTF-8"'},
        )
    if request.method not in SAFE_METHODS and not same_origin(request, config):
        return rejected(403, "Same-origin request required")
    response = await call_next(request)
    if login:
        response.set_cookie(
            cookie_name(config),
            issue_session(config),
            max_age=config.demo_session_seconds,
            httponly=True,
            secure=config.app_env == "production",
            samesite="strict",
            path="/",
        )
    response.headers["Cache-Control"] = "no-store"
    return response
