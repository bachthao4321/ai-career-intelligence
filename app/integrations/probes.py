from typing import Protocol

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class Probes(Protocol):
    async def postgres(self) -> None: ...

    async def qdrant(self) -> None: ...


class DependencyProbes:
    def __init__(self, engine: AsyncEngine, client: httpx.AsyncClient) -> None:
        self.engine = engine
        self.client = client

    async def postgres(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def qdrant(self) -> None:
        response = await self.client.get("/readyz")
        response.raise_for_status()
