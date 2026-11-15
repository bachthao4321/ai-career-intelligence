from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.routes.health import DependencyProbes


@pytest.mark.parametrize("status", [200, 503, 401])
async def test_qdrant_uses_ready_endpoint_and_checks_status(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/readyz"
        return httpx.Response(status)

    async with httpx.AsyncClient(
        base_url="http://qdrant:6333", transport=httpx.MockTransport(handler)
    ) as client:
        probes = DependencyProbes(MagicMock(spec=AsyncEngine), client)
        if status == 200:
            await probes.qdrant()
        else:
            with pytest.raises(httpx.HTTPStatusError):
                await probes.qdrant()
