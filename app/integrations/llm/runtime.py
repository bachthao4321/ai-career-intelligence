import asyncio
import hashlib
import json
import math
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.config import Settings
from app.db.models import LlmAttempt, LlmDecision
from app.integrations.llm.deepseek import reserve_daily_call
from app.schemas.ingestion import json_hash


class ModelFailure(RuntimeError):
    def __init__(self, code: str, *, retry: bool = False, split: bool = False, delay: float = 0):
        super().__init__(code)
        self.retry, self.split, self.delay = retry, split, delay


@dataclass
class CallBudget:
    limit: int
    used: int = 0
    dispatched: int = 0
    cache_hits: int = 0
    attempts_with_usage: int = 0
    tokens: dict[str, int] = field(default_factory=dict)

    def take(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


@dataclass
class Decision:
    value: dict[str, Any]
    cached: bool = False
    attempts: int = 0
    usage: dict[str, int] = field(default_factory=dict)


def advisory_key(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big", signed=True)


def token_usage(raw: Any) -> dict[str, int]:
    return (
        {
            key: value
            for key, value in raw.items()
            if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
            and type(value) is int
            and 0 <= value <= 1000000
        }
        if isinstance(raw, dict)
        else {}
    )


class ModelRuntime:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.engine, self.settings, self.transport = engine, settings, transport
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.bucket = json_hash(
            [
                settings.llm_base_url,
                settings.llm_model,
                json_hash(settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""),
            ]
        )
        self.signature = json_hash([self.bucket, settings.llm_model_revision, "json-chat-v1", 2048])

    async def _admit(self, task: str, key: str) -> uuid.UUID:
        settings = self.settings
        async with self.sessions.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": advisory_key("rate:" + self.bucket)},
            )
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            # Retain body-free attempt telemetry for seven days; daily quota is independent.
            await session.execute(
                delete(LlmAttempt).where(
                    LlmAttempt.started_at < now - timedelta(days=7), LlmAttempt.lease_until < now
                )
            )
            active = await session.scalar(
                select(func.count())
                .select_from(LlmAttempt)
                .where(
                    LlmAttempt.bucket == self.bucket,
                    LlmAttempt.status == "running",
                    LlmAttempt.lease_until > now,
                )
            )
            if (active or 0) >= settings.llm_concurrency:
                raise ModelFailure("provider_busy")
            last = await session.scalar(
                select(func.max(LlmAttempt.started_at)).where(LlmAttempt.bucket == self.bucket)
            )
            if last and (now - last).total_seconds() < settings.llm_min_interval_seconds:
                raise ModelFailure(
                    "rate_limited",
                    retry=True,
                    delay=settings.llm_min_interval_seconds - (now - last).total_seconds(),
                )
            attempt_id = uuid.uuid4()
            session.add(
                LlmAttempt(
                    id=attempt_id,
                    bucket=self.bucket,
                    task=task,
                    decision_key=key,
                    started_at=now,
                    lease_until=now + timedelta(seconds=settings.llm_timeout_seconds + 5),
                    status="running",
                    usage={},
                )
            )
        return attempt_id

    async def _finish(
        self, attempt: uuid.UUID, status: str, elapsed: float, usage: dict[str, int]
    ) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(LlmAttempt)
                .where(LlmAttempt.id == attempt)
                .values(status=status, elapsed_ms=max(0, round(elapsed * 1000)), usage=usage)
            )

    async def _wire(
        self, system: str, user: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, int]]:
        headers = {}
        if self.settings.llm_api_key:
            headers["Authorization"] = "Bearer " + self.settings.llm_api_key.get_secret_value()
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.llm_timeout_seconds,
                trust_env=False,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "POST",
                    self.settings.llm_base_url + "/chat/completions",
                    headers=headers,
                    json={
                        "model": self.settings.llm_model,
                        "temperature": 0,
                        "max_tokens": 2048,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                        ],
                    },
                ) as response:
                    raw = bytearray()
                    async for part in response.aiter_bytes():
                        if len(raw) + len(part) > 128000:
                            raise ModelFailure("provider_response_limit", split=True)
                        raw.extend(part)
                    if response.status_code in {401, 403}:
                        raise ModelFailure("provider_auth_error")
                    if response.status_code == 413:
                        raise ModelFailure("provider_context_limit", split=True)
                    if response.status_code == 429 or 500 <= response.status_code <= 599:
                        try:
                            delay = float(response.headers.get("retry-after", "0"))
                        except ValueError:
                            delay = 0
                        raise ModelFailure(
                            "provider_transient_error",
                            retry=True,
                            delay=min(2, max(0, delay)) if math.isfinite(delay) else 0,
                        )
                    if response.status_code != 200:
                        # Only a fixed provider error code can permit recursive split.
                        try:
                            code = json.loads(raw).get("error", {}).get("code")
                        except (ValueError, AttributeError):
                            code = None
                        if isinstance(code, str) and code in {
                            "context_length_exceeded",
                            "content_length_exceeded",
                        }:
                            raise ModelFailure("provider_context_limit", split=True)
                        raise ModelFailure("provider_http_error")
                    try:
                        body = json.loads(raw)
                        choice = body["choices"][0]
                        if choice["finish_reason"] != "stop":
                            raise ModelFailure("provider_incomplete_output", split=True)
                        value = json.loads(choice["message"]["content"])
                        if not isinstance(value, dict):
                            raise ValueError("Expected object")
                        return value, token_usage(body.get("usage"))
                    except (ValueError, KeyError, IndexError, TypeError) as exc:
                        raise ModelFailure("provider_invalid_json", split=True) from exc
        except httpx.RequestError as exc:
            raise ModelFailure("provider_connection_error", retry=True) from exc

    async def complete(
        self,
        task: str,
        system: str,
        user: dict[str, Any],
        validate: Callable[[dict[str, Any]], dict[str, Any]],
        budget: CallBudget,
    ) -> Decision:
        if task not in {"rerank-v1", "extract-batch-v1"}:
            raise ValueError("Shared runtime permits public-job decisions only")
        if len(json.dumps([system, user], ensure_ascii=False).encode()) > 64000:
            raise ModelFailure("model_input_limit", split=True)
        key = json_hash([self.signature, task, system, user])
        lock_key = advisory_key("decision:" + key)
        try:
            async with asyncio.timeout(self.settings.llm_timeout_seconds):
                # A transaction-scoped lock is released even if cancellation happens
                # immediately after acquisition, before Python can enter a finally block.
                async with self.engine.begin() as lock:
                    acquired = await lock.scalar(
                        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key}
                    )
                    if not acquired:
                        raise ModelFailure("decision_in_progress")
                    return await self._complete_locked(task, key, system, user, validate, budget)
        except TimeoutError as exc:
            raise ModelFailure("model_deadline_exceeded") from exc
        except SQLAlchemyError as exc:
            raise ModelFailure("model_state_unavailable") from exc

    async def _complete_locked(
        self,
        task: str,
        key: str,
        system: str,
        user: dict[str, Any],
        validate: Callable[[dict[str, Any]], dict[str, Any]],
        budget: CallBudget,
    ) -> Decision:
        async with self.sessions() as session:
            cached = await session.scalar(
                select(LlmDecision).where(
                    LlmDecision.key == key, LlmDecision.expires_at > func.clock_timestamp()
                )
            )
        if cached:
            try:
                value = validate(cached.value)
                budget.cache_hits += 1
                return Decision(value, cached=True)
            except ValueError:
                async with self.sessions.begin() as session:
                    await session.execute(delete(LlmDecision).where(LlmDecision.key == key))
        limit = min(self.settings.llm_daily_call_limit, self.settings.llm_global_daily_call_limit)
        if limit <= 0:
            raise ModelFailure("model_daily_budget_disabled")
        used = 0
        # Local rate waits don't consume network retries or daily calls; wall deadline bounds them.
        while used < self.settings.llm_max_attempts:
            if budget.used >= budget.limit:
                raise ModelFailure("operation_call_limit")
            try:
                attempt = await self._admit(task, key)
            except ModelFailure as exc:
                if str(exc) != "rate_limited":
                    raise
                await asyncio.sleep(exc.delay + random.uniform(0, 0.05))
                continue
            status = "cancelled"
            usage: dict[str, int] = {}
            dispatched = False
            started = time.monotonic()
            try:
                if not budget.take():
                    raise ModelFailure("operation_call_limit")
                if not await reserve_daily_call(self.engine, limit):
                    raise ModelFailure("model_daily_budget_exhausted")
                used += 1
                budget.dispatched += 1
                dispatched = True
                value, usage = await self._wire(system, user)
                try:
                    value = validate(value)
                except ValueError as exc:
                    raise ModelFailure("model_schema_or_evidence_invalid", split=True) from exc
                status = "ok"
            except ModelFailure as exc:
                status = str(exc)
                if not exc.retry or used >= self.settings.llm_max_attempts:
                    raise
                await asyncio.sleep(
                    max(exc.delay, 0.15 * 2 ** (used - 1)) + random.uniform(0, 0.05)
                )
                continue
            finally:
                if dispatched and usage:
                    budget.attempts_with_usage += 1
                    for name, token_count in usage.items():
                        budget.tokens[name] = budget.tokens.get(name, 0) + token_count
                await self._finish(attempt, status, time.monotonic() - started, usage)
            async with self.sessions.begin() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(44551182002)"))
                await session.execute(
                    delete(LlmDecision).where(LlmDecision.expires_at <= func.clock_timestamp())
                )
                count = await session.scalar(select(func.count()).select_from(LlmDecision))
                if (count or 0) < self.settings.llm_cache_capacity:
                    await session.execute(
                        insert(LlmDecision)
                        .values(
                            key=key,
                            task=task,
                            value=value,
                            expires_at=func.clock_timestamp()
                            + timedelta(seconds=self.settings.llm_cache_ttl_seconds),
                        )
                        .on_conflict_do_update(
                            index_elements=[LlmDecision.key],
                            set_={
                                "value": value,
                                "expires_at": func.clock_timestamp()
                                + timedelta(seconds=self.settings.llm_cache_ttl_seconds),
                            },
                        )
                    )
            return Decision(value, attempts=used, usage=usage)
        raise ModelFailure("operation_call_limit")
