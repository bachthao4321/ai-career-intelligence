"""Demo password gate: signed session cookies, Basic auth, client identity and origin checks."""

import base64
import hashlib
import hmac
import ipaddress
import secrets
import time
from urllib.parse import urlsplit

from fastapi import Request

from app.core.config import Settings


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
