import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import Settings
from app.services.translation.descriptions import (
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
    monkeypatch.setattr("app.services.translation.descriptions.reserve_daily_call", reserve)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        assert body["response_format"]["type"] == "json_schema"
        lines = json.loads(body["messages"][1]["content"])["lines"]
        schema = body["response_format"]["json_schema"]["schema"]["properties"]["lines"]
        assert schema["minItems"] == schema["maxItems"] == len(lines)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"lines": lines})},
                    }
                ]
            },
        )

    config = settings.model_copy(update={"agent_model_enabled": True, "agent_daily_call_limit": 10})
    translator = DescriptionTranslator(config, None, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    text = "Svensk beskrivning och teknik.\n" * 180
    first = await translator.translate(text, "vi", "jobtech", "https://example.com")
    again = await translator.translate(text, "vi", "jobtech", "https://example.com")
    assert first == again == text
    assert len(requests) == len(split_description(text)) == reserve.await_count
    sent = sum(len(json.loads(r["messages"][1]["content"])["lines"]) for r in requests)
    assert sent == 180
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
    monkeypatch.setattr("app.services.translation.descriptions.reserve_daily_call", reserve)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"lines": ["partial"]})},
                    }
                ]
            },
        )

    config = settings.model_copy(update={"agent_model_enabled": True, "agent_daily_call_limit": 10})
    translator = DescriptionTranslator(config, None, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="model_quota_exhausted"):
        await translator.translate("Svenska ord " * 500, "vi", "jobtech", "https://example.com")
    assert not translator._cache


async def test_job_translation_returns_title_and_description_together() -> None:
    from types import SimpleNamespace

    from app.services.jobs import translate_job

    job = SimpleNamespace(
        title="Unreviewed IT role",
        description="Original full text",
        source="jobtech",
        source_url="https://example.com/job",
    )
    translator = SimpleNamespace(translate=AsyncMock(side_effect=["Vai trò IT", "Mô tả đã dịch"]))
    result = await translate_job(job, "vi", translator)  # type: ignore[arg-type]
    assert result.title == "Vai trò IT"
    assert result.title_source == "machine"
    assert result.text == "Mô tả đã dịch"
    assert translator.translate.await_count == 2


async def test_reviewed_title_reused_without_extra_model_call() -> None:
    from types import SimpleNamespace

    from app.services.jobs import translate_job

    job = SimpleNamespace(
        title="Backend Developer",
        description="Original full text",
        source="jobtech",
        source_url="https://example.com/job",
    )
    translator = SimpleNamespace(translate=AsyncMock(return_value="Mô tả đã dịch"))
    result = await translate_job(job, "vi", translator)  # type: ignore[arg-type]
    assert result.title == "Lập trình viên Backend"
    assert result.title_source == "reviewed"
    assert translator.translate.await_count == 1


@pytest.mark.parametrize("disable", [False, True])
async def test_thinking_switch_is_sent_only_when_configured(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, disable: bool
) -> None:
    monkeypatch.setattr(
        "app.services.translation.descriptions.reserve_daily_call", AsyncMock(return_value=True)
    )
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        content = json.dumps({"lines": ["Bản dịch"]})
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
        )

    config = settings.model_copy(
        update={
            "agent_model_enabled": True,
            "agent_daily_call_limit": 10,
            "agent_model_disable_thinking": disable,
        }
    )
    translator = DescriptionTranslator(config, None, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    await translator.translate("Short text", "vi", "jobtech", "https://example.com")
    expected = {"enable_thinking": False} if disable else None
    assert bodies[0].get("chat_template_kwargs") == expected


@pytest.mark.parametrize(
    ("returned", "expected"),
    [
        (["Tiêu đề", "  - Mục một", "Mục hai"], None),
        (["Tiêu đề", "Mục một gộp mục hai"], "model_unavailable"),
    ],
)
async def test_line_breaks_survive_and_merged_lines_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    returned: list[str],
    expected: str | None,
) -> None:
    monkeypatch.setattr(
        "app.services.translation.descriptions.reserve_daily_call", AsyncMock(return_value=True)
    )
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(json.loads(request.content)["messages"][1]["content"])["lines"])
        content = json.dumps({"lines": returned})
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
        )

    config = settings.model_copy(update={"agent_model_enabled": True, "agent_daily_call_limit": 10})
    translator = DescriptionTranslator(config, None, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    source = "Rubrik\n\n  - Punkt ett\nPunkt två\n"
    if expected:
        with pytest.raises(ValueError, match=expected):
            await translator.translate(source, "vi", "jobtech", "https://example.com")
        assert not translator._cache
        return
    result = await translator.translate(source, "vi", "jobtech", "https://example.com")
    assert sent == [["Rubrik", "- Punkt ett", "Punkt två"]]
    assert result == "Tiêu đề\n\n  - Mục một\nMục hai\n"
