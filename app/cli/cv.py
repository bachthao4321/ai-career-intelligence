import asyncio
import json

from app.core.config import Settings
from app.db.session import create_engine
from app.services.cv.service import CVService


async def cleanup() -> int:
    settings = Settings()
    engine = create_engine(settings)
    try:
        return await CVService(engine, settings).cleanup()
    finally:
        await engine.dispose()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Purge expired CV rows without reading their text")
    parser.add_argument("command", choices=["cleanup"])
    parser.parse_args()
    try:
        print(json.dumps({"removed": asyncio.run(cleanup())}))
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "hint": "Check DB and migrations"}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
