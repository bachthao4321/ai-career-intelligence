import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import Settings
from app.ingestion.description_translation import (
    DescriptionTranslator,
    detect_language,
    split_description,
)


def test_language_detection_is_conservative() -> None:
    assert (
        detect_language("The team and you will work with our systems and the team in our office.")
        == "en"
    )
    assert (
        detect_language(
            "Du kommer att arbeta med vårt team och våra system och vi söker dig som vill vara med."
        )
        == "other"
    )
    assert detect_language("Python React SQL") == "unknown"
    assert detect_language("arbitrary text", "vi") == "vi"


def test_chunking_preserves_all_input_and_rejects_oversized() -> None:
    text = ("Ett stycke med ord och innehåll.\n" * 300).strip()
    chunks = split_description(text)
    assert len(chunks) > 1 and "".join(chunks) == text
    assert all(len(c) <= 3500 for c in chunks)
    with pytest.raises(ValueError, match="description_too_long"):
        split_description("x" * 16001)


async def test_translation_is_complete_cached_and_budgeted(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    reserve = AsyncMock(return_value=True)
    monkeypatch.setattr("app.ingestion.description_translation.reserve_daily_call", reserve)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        assert body["response_format"]["type"] == "json_schema"
        source = json.loads(body["messages"][1]["content"])["description"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"text": source.strip()})},
                    }
                ]
            },
        )

    config = settings.model_copy(update={"agent_model_enabled": True, "agent_daily_call_limit": 10})
    translator = DescriptionTranslator(config, None, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    text = "Svensk beskrivning och teknik.\n" * 180
    first = await translator.translate(text, "vi", "jobtech", "https://example.com")
    again = await translator.translate(text, "vi", "jobtech", "https://example.com")
    assert first == again and len(requests) == len(split_description(text)) == reserve.await_count
    assert "".join(json.loads(r["messages"][1]["content"])["description"] for r in requests) == text
    await translator.translate(text, "en", "jobtech", "https://example.com")
    assert reserve.await_count == 2 * len(split_description(text))


async def test_restricted_source_never_sent_to_hosted_model(settings: Settings) -> None:
    config = settings.model_copy(
        update={
            "agent_model_enabled": True,
            "agent_model_base_url": "https://hosted.example/v1",
            "app_env": "local",
        }
    )
    translator = DescriptionTranslator(config, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="restricted_source"):
        await translator.translate("private content", "vi", "itviec", "https://itviec.com/jobs/1")


async def test_failure_or_quota_never_return_partial_translation(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    reserve = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr("app.ingestion.description_translation.reserve_daily_call", reserve)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"text": "partial"})},
                    }
                ]
            },
        )

    config = settings.model_copy(update={"agent_model_enabled": True, "agent_daily_call_limit": 10})
    translator = DescriptionTranslator(config, None, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="model_quota_exhausted"):
        await translator.translate("Svenska ord " * 500, "vi", "jobtech", "https://example.com")
    assert not translator._cache
