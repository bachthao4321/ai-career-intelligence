import argparse
import asyncio
import os

import httpx

from app.core.config import Settings
from app.db.session import create_engine
from app.integrations.embeddings import LocalMiniLM
from app.integrations.qdrant import QdrantStore
from app.schemas.ask import AskRequest
from app.services.ask.analytics import AnalyticsExecutor, provision_role
from app.services.ask.planner import ModelPlanner
from app.services.ask.workflow import CareerAgent
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


HINT = "Check migrations, restricted role, analytics URL and model config."


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="python -m app.cli ask", description="Analytics role provisioning and career agent"
    )
    sub = root.add_subparsers(dest="command", required=True)
    provision = sub.add_parser("provision")
    provision.add_argument("--scope", choices=["local", "public"], required=True)
    provision.add_argument("--role", required=True)
    question = sub.add_parser("question")
    question.add_argument("question")
    return root


async def run(args: argparse.Namespace) -> dict[str, object]:
    settings = Settings()
    if args.command == "provision":
        await asyncio.to_thread(
            provision_role,
            settings.database_url.get_secret_value(),
            settings.analytics_schema,
            args.role,
            os.environ["ANALYTICS_ROLE_PASSWORD"],
            args.scope,
        )
        return {"role": args.role, "scope": args.scope, "provisioned": True}
    return await ask(settings, args.question)
