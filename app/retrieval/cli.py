import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

from app.core.config import Settings
from app.db.session import create_engine
from app.retrieval.contracts import SearchFilters, SearchRequest
from app.retrieval.documents import load_documents
from app.retrieval.embeddings import LocalMiniLM, download_model
from app.retrieval.evaluation import evaluate, prepare_pilot
from app.retrieval.indexing import index_documents, namespace
from app.retrieval.qdrant import QdrantStore
from app.retrieval.service import SearchService


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="M2 local hybrid retrieval and index operations")
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
    search.add_argument("--mode", choices=["bm25", "dense", "hybrid"], default="bm25")
    search.add_argument("--source")
    search.add_argument("--location")
    search.add_argument("--top-k", type=int, default=5)
    prepare = commands.add_parser("prepare-eval")
    prepare.add_argument("--output", type=Path, default=Path("evals/jobtech-pilot.json"))
    prepare.add_argument("--families", type=Path, default=Path("evals/query_families.json"))
    evaluation = commands.add_parser("evaluate")
    evaluation.add_argument("dataset", type=Path)
    evaluation.add_argument("--output", type=Path, default=Path("data/evaluations/m2-pilot.json"))
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
            service = SearchService(engine, settings, embedder, store)
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
                    )
                )
                return response.model_dump(mode="json")
            if args.command == "prepare-eval":
                docs = await load_documents(engine, settings, SearchFilters(source="jobtech"))
                return await asyncio.to_thread(prepare_pilot, docs, args.families, args.output)
            return await evaluate(service, args.dataset, args.output)
    finally:
        await engine.dispose()


def main() -> None:
    args = parser().parse_args()
    try:
        value = asyncio.run(run(args))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": type(exc).__name__,
                    "hint": "Check migrations, local model assets, Qdrant and dataset fingerprint.",
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
