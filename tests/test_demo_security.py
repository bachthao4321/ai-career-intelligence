import base64
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from starlette.requests import Request

from app.core.config import Settings
from app.core.security import demo
from app.main import create_app


def secured(settings: Settings, **updates) -> Settings:
    return Settings.model_validate(
        settings.model_dump()
        | {
            "demo_auth_enabled": True,
            "demo_password": SecretStr("fixture-password-12345"),
            "demo_cookie_secret": SecretStr("c" * 64),
            "admin_key": SecretStr("a" * 64),
            **updates,
        }
    )


def basic() -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(b"viewer:fixture-password-12345").decode()}


def test_production_requires_all_security_controls(settings: Settings) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(settings.model_dump() | {"app_env": "production"})
    with pytest.raises(ValidationError):
        secured(settings, demo_cookie_secret=SecretStr("a" * 64))
    with pytest.raises(ValidationError):
        secured(settings, public_origin="http://demo.example")


def test_cookie_integrity_expiry_and_password_rotation(settings: Settings, monkeypatch) -> None:
    config = secured(settings)
    monkeypatch.setattr(demo.time, "time", lambda: 1000)
    token = demo.issue_session(config)
    assert demo.valid_session(config, token)
    assert not demo.valid_session(config, token[:-2] + "ff")
    assert not demo.valid_session(config, "x" * 300)
    rotated = secured(settings, demo_password=SecretStr("another-fixture-password"))
    assert not demo.valid_session(rotated, token)
    monkeypatch.setattr(demo.time, "time", lambda: 1000 + config.demo_session_seconds)
    assert not demo.valid_session(config, token)


def test_untrusted_forwarding_headers_never_change_client_identity(settings: Settings) -> None:
    config = secured(settings, trusted_proxy_ip="172.30.88.2")

    def identity(peer, forwarded):
        return demo.client_hash(
            Request(
                {
                    "type": "http",
                    "client": (peer, 123),
                    "headers": [(b"x-forwarded-for", forwarded.encode())],
                }
            ),
            config,
        )

    assert identity("192.0.2.1", "1.1.1.1") == identity("192.0.2.1", "2.2.2.2")
    assert identity("172.30.88.2", "1.1.1.1") != identity("172.30.88.2", "2.2.2.2")
    assert identity("172.30.88.2", "invalid") == identity("172.30.88.2", "1.1.1.1, 2.2.2.2")


def test_gate_protects_pages_apis_docs_and_preserves_health(settings: Settings) -> None:
    config = secured(settings)
    with TestClient(create_app(config)) as client:
        for path in [
            "/",
            "/docs",
            "/openapi.json",
            "/platform/overview",
            "/jobs",
            "/assets/app.js",
        ]:
            response = client.get(path)
            assert response.status_code == 401
            assert response.headers["cache-control"] == "no-store"
        assert client.get("/health").status_code == 200
        assert client.get("/", headers={"Authorization": "Basic invalid"}).status_code == 401
        response = client.get("/", headers=basic())
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        assert client.get("/docs").status_code == 200
        assert (
            client.post(
                "/search", headers={"Origin": "https://evil.example"}, json={"query": "Python"}
            ).status_code
            == 403
        )
        assert client.post("/search", json={"query": "Python"}).status_code == 403
        assert (
            client.post(
                "/admin/import", headers={"Origin": "http://testserver"}, json={"records": []}
            ).status_code
            == 403
        )


def test_rate_limit_is_before_auth_and_fails_closed(settings: Settings, monkeypatch) -> None:
    quota = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr("app.core.middleware.reserve_client", quota)
    config = secured(settings, client_limits_enabled=True)
    with TestClient(create_app(config)) as client:
        assert client.get("/").status_code == 401
        response = client.get("/", headers=basic())
        assert response.status_code == 429 and response.headers["retry-after"] == "60"


def test_production_revalidation_prevents_model_copy_bypass(settings: Settings) -> None:
    with pytest.raises(ValidationError):
        create_app(settings.model_copy(update={"app_env": "production"}))
