"""CI-only real PostgreSQL/Qdrant restore drill over synthetic jobs and 3D vectors."""

import argparse
import asyncio
import json
import os
import secrets

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.models import Job
from app.db.session import create_engine
from app.integrations.qdrant import QdrantStore
from app.services.search.indexing import index_documents, namespace
from scripts.operations import ROOT, Compose, backup, restore_drill
from tests.retrieval_helpers import TestEmbedder


async def seed() -> None:
    if os.environ.get("M6_BACKUP_FIXTURE") != "1" or os.environ.get("APP_ENV") != "test":
        raise ValueError("Synthetic restore smoke requires explicit test environment flags")
    settings = Settings()
    engine = create_engine(settings)
    try:
        async with async_sessionmaker(engine)() as session:
            sources = list(await session.scalars(select(Job.source)))
            if not sources or len(sources) > 20 or set(sources) != {"synthetic"}:
                raise ValueError("This fixture is permitted only on a small synthetic database")
        encoder = TestEmbedder()
        async with httpx.AsyncClient(
            base_url=settings.qdrant_url, trust_env=False, timeout=30
        ) as client:
            store = QdrantStore(client, namespace(settings, encoder), encoder.dimension)
            await index_documents(engine, settings, encoder, store)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="ai-career-intelligence")
    args = parser.parse_args()
    asyncio.run(seed())
    path = ROOT / "backups" / ("ci-synthetic-" + secrets.token_hex(4))
    created = backup(Compose(ROOT / "compose.yaml", args.project), path)
    assert created["points"] > 0
    result = restore_drill(path)
    assert result["vector_queries_passed"] and result["row_and_content_hashes_match"]
    print(json.dumps({"synthetic_fixture_only": True, **result}, indent=2))


if __name__ == "__main__":
    main()
