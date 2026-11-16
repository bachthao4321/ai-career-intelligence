import argparse
from dataclasses import asdict
from typing import Any

from app.core.config import Settings
from app.db.session import create_engine
from app.services.translation.titles import translate_titles

HINT = "Title translation stopped; inspect quota and model configuration."


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="python -m app.cli translate",
        description="Translate job titles to English and Vietnamese within the model budget",
    )
    root.add_argument("command", choices=["titles"])
    root.add_argument("--max-calls", type=int, default=0)
    root.add_argument("--batch-size", type=int, default=16)
    root.add_argument("--limit", type=int, default=1000)
    return root


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = Settings()
    engine = create_engine(settings)
    try:
        report = await translate_titles(
            engine, settings, max_calls=args.max_calls, batch_size=args.batch_size, limit=args.limit
        )
        return asdict(report)
    finally:
        await engine.dispose()
