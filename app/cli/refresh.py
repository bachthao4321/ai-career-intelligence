"""Scheduled refresh: fetch public sources, import changes, then update the vector index.

Personal-research sources are never fetched here. Snapshots go to a temporary directory
because the database is the system of record; one failing source never blocks the others.
"""

import asyncio
import json
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.session import create_engine
from app.ingestion.employers import fetch_employers
from app.ingestion.service import import_records
from app.ingestion.sources import fetch_jobtech, fetch_remotive, load_records, write_snapshot
from app.integrations.embeddings import EmbeddingUnavailable, LocalMiniLM
from app.integrations.qdrant import QdrantStore, VectorBackendUnavailable
from app.services.search.indexing import IndexAlreadyRunning, index_documents, namespace

PUBLIC_SOURCES = ("jobtech", "remotive", "employers")
MIN_INTERVAL_SECONDS = 900  # Be polite to source APIs: never poll more than every 15 minutes.
SOURCE_ERRORS = (OSError, ValueError, KeyError, httpx.HTTPError, SQLAlchemyError)


async def fetch_snapshot(source: str, directory: Path) -> Path:
    path = directory / f"{source}.json"
    if source == "employers":
        rows, _ = await asyncio.to_thread(fetch_employers)
        await asyncio.to_thread(write_snapshot, path, rows)
    elif source == "jobtech":
        await fetch_jobtech(path, limit=150)
    else:
        await fetch_remotive(path, limit=150)
    return path


async def import_source(engine: AsyncEngine, settings: Settings, source: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"refresh-{source}-") as directory:
        path = await fetch_snapshot(source, Path(directory))
        records, errors = await asyncio.to_thread(load_records, path)
    if not records:
        return {"valid": 0, "skipped": len(errors)}
    report = await import_records(engine, records, settings)
    return {
        "valid": len(records),
        "skipped": len(errors),
        "inserted": report.inserted,
        "updated": report.updated,
        "unchanged": report.unchanged,
    }


async def update_index(engine: AsyncEngine, settings: Settings) -> dict[str, Any]:
    embedder = LocalMiniLM(settings.embedding_model_dir, threads=settings.embedding_threads)
    try:
        async with httpx.AsyncClient(
            base_url=settings.qdrant_url, timeout=settings.vector_timeout_seconds, trust_env=False
        ) as client:
            store = QdrantStore(client, namespace(settings, embedder), embedder.dimension)
            return asdict(await index_documents(engine, settings, embedder, store))
    except (EmbeddingUnavailable, VectorBackendUnavailable, IndexAlreadyRunning) as exc:
        # BM25 keeps working without the index; report and retry on the next cycle.
        return {"error": type(exc).__name__}


async def refresh_once(settings: Settings, sources: tuple[str, ...]) -> dict[str, Any]:
    started = datetime.now(UTC)
    engine = create_engine(settings)
    results: dict[str, Any] = {}
    try:
        for source in sources:
            try:
                results[source] = await import_source(engine, settings, source)
            except SOURCE_ERRORS as exc:
                results[source] = {"error": type(exc).__name__}
        index = await update_index(engine, settings)
    finally:
        await engine.dispose()
    return {
        "started_at": started.isoformat(),
        "seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
        "sources": results,
        "index": index,
    }


def parse_sources(value: str) -> tuple[str, ...]:
    names = tuple(dict.fromkeys(name.strip() for name in value.split(",") if name.strip()))
    unknown = set(names) - set(PUBLIC_SOURCES)
    if not names or unknown:
        raise ValueError(f"Sources must be a subset of {', '.join(PUBLIC_SOURCES)}")
    return names


async def refresh(
    settings: Settings, sources: tuple[str, ...], every: int | None
) -> dict[str, Any]:
    if every is None:
        return await refresh_once(settings, sources)
    if every < MIN_INTERVAL_SECONDS:
        raise ValueError(f"--every must be at least {MIN_INTERVAL_SECONDS} seconds")
    while True:
        report = await refresh_once(settings, sources)
        print(json.dumps(report, ensure_ascii=False), flush=True)
        await asyncio.sleep(every)
