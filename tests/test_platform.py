from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_platform_shell_assets_and_security_headers(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert '<html lang="vi">' in response.text
        assert all(f'id="view-{view}"' in response.text for view in ("jobs", "analytics", "cv"))
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["cache-control"] == "no-store"
        for asset in (
            "app.js",
            "jobs.js",
            "cv.js",
            "analytics.js",
            "logic.mjs",
            "styles.css",
            "favicon.svg",
        ):
            assert client.get("/assets/" + asset).status_code == 200
        assert client.get("/assets/../../.env").status_code == 404
        assert client.get("/not-an-api-route").status_code == 404
