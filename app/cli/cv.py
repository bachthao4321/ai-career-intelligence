import argparse

from app.core.config import Settings
from app.db.session import create_engine
from app.services.cv.service import CVService

HINT = "Check DB and migrations"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="python -m app.cli cv", description="Purge expired CV rows without reading their text"
    )
    root.add_argument("command", choices=["cleanup"])
    return root


async def run(args: argparse.Namespace) -> dict[str, int]:
    settings = Settings()
    engine = create_engine(settings)
    try:
        return {"removed": await CVService(engine, settings).cleanup()}
    finally:
        await engine.dispose()
