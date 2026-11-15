import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.models import AliasCandidate
from app.db.session import create_engine
from app.ingestion.batching import BatchExtractor
from app.ingestion.employers import fetch_employers
from app.ingestion.enrichment import enrich_existing
from app.ingestion.itviec import fetch_itviec
from app.ingestion.model_extraction import DeepSeekExtractor
from app.ingestion.reporting import build_report
from app.ingestion.service import import_records, review_alias
from app.ingestion.sources import fetch_jobtech, fetch_remotive, load_records, write_snapshot
from app.integrations.llm.runtime import ModelRuntime

HINT = "Ingestion failed; check configuration, source format and migration state."


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="python -m app.cli ingest",
        description="Operator-only ingestion; no public write endpoints.",
    )
    commands = root.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch")
    fetch.add_argument("source", choices=["jobtech", "remotive", "itviec", "employers"])
    fetch.add_argument("--limit", type=int)
    fetch.add_argument("--offset", type=int, default=0, help="JobTech result offset (default: 0)")
    ingest = commands.add_parser("import")
    ingest.add_argument("path", type=Path)
    ingest.add_argument(
        "--extractor", choices=["deterministic", "deepseek", "batch"], default="deterministic"
    )
    ingest.add_argument("--max-calls", type=int, default=0)
    ingest.add_argument("--retry-failed", action="store_true")
    report = commands.add_parser("report")
    report.add_argument("--output", type=Path)
    report.add_argument("--sample-size", type=int, default=10)
    commands.add_parser("aliases")
    commands.add_parser("enrich", help="Recompute requirement levels and job facts for stored jobs")
    review = commands.add_parser("review-alias")
    review.add_argument("candidate_id", type=uuid.UUID)
    review.add_argument("--decision", choices=["approve", "reject"], required=True)
    review.add_argument("--reviewer", required=True)
    return root


def write_report(path: Path, value: dict[str, Any]) -> None:
    # Reports include source excerpts: keep them in the ignored local output directory.
    if not path.resolve().is_relative_to(Path("data/processed").resolve()):
        raise ValueError("Report output must be under data/processed/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "fetch" and args.source == "employers":
        path = Path("data/raw") / f"employers-{datetime.now(UTC).date()}.json"
        rows, counts = await asyncio.to_thread(fetch_employers)
        await asyncio.to_thread(write_snapshot, path, rows)
        records, errors = await asyncio.to_thread(load_records, path)
        return {"snapshot": str(path), "valid_jobs": len(records), "skipped": errors, **counts}
    if args.command == "fetch":
        if args.offset and args.source != "jobtech":
            raise ValueError("Only JobTech supports result offsets")
        suffix = f"-offset-{args.offset}" if args.offset else ""
        path = Path("data/raw") / f"{args.source}-{datetime.now(UTC).date()}{suffix}.json"
        fetcher = {"jobtech": fetch_jobtech, "remotive": fetch_remotive, "itviec": fetch_itviec}[
            args.source
        ]
        limit = args.limit if args.limit is not None else (10 if args.source == "itviec" else 150)
        if args.source == "jobtech":
            await fetch_jobtech(path, limit=limit, offset=args.offset)
        else:
            await fetcher(path, limit=limit)
        records, errors = await asyncio.to_thread(load_records, path)
        return {"snapshot": str(path), "valid_jobs": len(records), "skipped": errors}
    settings = Settings()
    engine = create_engine(settings)
    try:
        if args.command == "import":
            records, errors = await asyncio.to_thread(load_records, args.path)
            if not records:
                raise ValueError("No valid IT records to import")
            extractor = DeepSeekExtractor(settings) if args.extractor == "deepseek" else None
            result = await import_records(
                engine,
                records,
                settings,
                extractor=extractor,
                batch_extractor=(
                    BatchExtractor(ModelRuntime(engine, settings))
                    if args.extractor == "batch"
                    else None
                ),
                max_calls=args.max_calls,
                retry_failed=args.retry_failed,
            )
            return {**result.as_dict(), "valid_records": len(records), "skipped": errors}
        if args.command == "report":
            report = await build_report(engine, sample_size=args.sample_size)
            if args.output:
                await asyncio.to_thread(write_report, args.output, report)
                return {key: value for key, value in report.items() if key != "samples"} | {
                    "output": str(args.output)
                }
            return report
        if args.command == "enrich":
            return await enrich_existing(engine)
        if args.command == "aliases":
            async with async_sessionmaker(engine)() as session:
                candidates = (
                    await session.scalars(
                        select(AliasCandidate).where(AliasCandidate.status == "pending").limit(100)
                    )
                ).all()
                return {
                    "pending": [
                        {
                            "id": str(c.id),
                            "alias": c.surface,
                            "skill": c.skill_slug,
                            "confidence": c.confidence,
                            "job_id": str(c.job_id),
                        }
                        for c in candidates
                    ]
                }
        await review_alias(engine, args.candidate_id, args.decision, args.reviewer)
        return {"candidate_id": str(args.candidate_id), "decision": args.decision}
    finally:
        await engine.dispose()
