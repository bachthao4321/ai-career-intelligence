import argparse
import asyncio
import json
import os
import sys

import httpx

from app.core.config import Settings
from app.db.session import create_engine
from app.integrations.embeddings import LocalMiniLM
from app.integrations.qdrant import QdrantStore
from app.services.ask.analytics import AnalyticsExecutor, provision_role
from app.services.ask.planner import ModelPlanner
from app.services.ask.workflow import AskRequest, CareerAgent
from app.services.search.indexing import namespace
from app.services.search.service import SearchService


async def ask(settings: Settings, question: str) -> dict[str, object]:
    engine = create_engine(settings)
    analytics = AnalyticsExecutor(settings)
    encoder = LocalMiniLM(settings.embedding_model_dir, threads=settings.embedding_threads)
    try:
        async with httpx.AsyncClient(
            base_url=settings.qdrant_url, timeout=30, trust_env=False
        ) as client:
            search = SearchService(
                engine,
                settings,
                encoder,
                QdrantStore(client, namespace(settings, encoder), encoder.dimension),
            )
            agent = CareerAgent(search, analytics, ModelPlanner(settings, engine))
            return (await agent.ask(AskRequest(question=question))).model_dump(mode="json")
    finally:
        await analytics.close()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="M3 role provisioning and bounded career agent")
    sub = parser.add_subparsers(dest="command", required=True)
    provision = sub.add_parser("provision")
    provision.add_argument("--scope", choices=["local", "public"], required=True)
    provision.add_argument("--role", required=True)
    question = sub.add_parser("ask")
    question.add_argument("question")
    args = parser.parse_args()
    try:
        settings = Settings()
        if args.command == "provision":
            provision_role(
                settings.database_url.get_secret_value(),
                settings.analytics_schema,
                args.role,
                os.environ["ANALYTICS_ROLE_PASSWORD"],
                args.scope,
            )
            result = {"role": args.role, "scope": args.scope, "provisioned": True}
        else:
            result = asyncio.run(ask(settings, args.question))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": type(exc).__name__,
                    "hint": "Check migrations, restricted role, analytics URL and model config.",
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
