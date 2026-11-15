import argparse
import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

from app.core.config import Settings
from app.db.session import create_engine
from app.integrations.embeddings import LocalMiniLM, download_model
from app.integrations.llm.runtime import ModelRuntime
from app.integrations.qdrant import QdrantStore
from app.schemas.search import SearchFilters, SearchRequest
from app.services.search.indexing import index_documents, namespace
from app.services.search.reranking import Reranker
from app.services.search.service import SearchService

HINT = "Check migrations, local model assets and Qdrant."


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="python -m app.cli search", description="Search index operations"
    )
    root.add_argument(
        "--public", action="store_true", help="Use public scope; exclude personal-research sources"
    )
    commands = root.add_subparsers(dest="command", required=True)
    model = commands.add_parser("download-model")
    model.add_argument("--directory", type=Path, default=Path("models/multilingual-minilm"))
    index = commands.add_parser("index")
    index.add_argument("--max-jobs", type=int)
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--mode", choices=["bm25", "dense", "hybrid"], default="hybrid")
    search.add_argument("--source")
    search.add_argument("--location")
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--rerank", action="store_true")
    return root


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "download-model":
        return await asyncio.to_thread(download_model, args.directory)
    settings = Settings()
    if args.public:
        settings = settings.model_copy(update={"app_env": "production"})
    engine = create_engine(settings)
    embedder = LocalMiniLM(settings.embedding_model_dir, threads=settings.embedding_threads)
    try:
        async with httpx.AsyncClient(
            base_url=settings.qdrant_url, timeout=settings.vector_timeout_seconds, trust_env=False
        ) as client:
            store = QdrantStore(client, namespace(settings, embedder), embedder.dimension)
            service = SearchService(
                engine,
                settings,
                embedder,
                store,
                reranker=Reranker(ModelRuntime(engine, settings), settings),
            )
            if args.command == "index":
                report = await index_documents(
                    engine, settings, embedder, store, max_jobs=args.max_jobs
                )
                return {
                    "collection": store.collection,
                    "model_signature": embedder.signature,
                    **asdict(report),
                }
            if args.command == "search":
                response = await service.search(
                    SearchRequest(
                        query=args.query,
                        mode=args.mode,
                        top_k=args.top_k,
                        filters=SearchFilters(source=args.source, location=args.location),
                        rerank=args.rerank,
                    )
                )
                return response.model_dump(mode="json")
            raise ValueError(args.command)
    finally:
        await engine.dispose()
