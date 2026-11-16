import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.ingestion.extraction import SkillCatalog
from app.ingestion.model_extraction import DeepSeekExtractor, ExtractionError
from app.schemas.ingestion import JobRecord


@pytest.fixture
def llm_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"deepseek_api_key": SecretStr("synthetic-secret"), "deepseek_model": "test-model"}
    )


async def test_deepseek_json_mode_uses_validated_schema_and_records_usage(
    llm_settings: Settings, record: JobRecord
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        assert request.url == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == "Bearer synthetic-secret"
        assert data["response_format"] == {"type": "json_object"}
        assert data["max_tokens"] == 4096
        assert "untrusted" in data["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"skills":[]}'}}],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 5,
                    "total_tokens": 25,
                    "other": "ignored",
                },
            },
        )

    output, usage = await DeepSeekExtractor(
        llm_settings, transport=httpx.MockTransport(handler)
    ).extract(record, SkillCatalog.load())
    assert output.skills == []
    assert usage == {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}


@pytest.mark.parametrize(
    "body,expected",
    [
        ({"choices": []}, "provider_invalid_output"),
        (
            {
                "choices": [{"finish_reason": "stop", "message": {"content": '{"skills":[]}'}}],
                "usage": "bad",
            },
            "provider_invalid_output",
        ),
        (
            {"choices": [{"finish_reason": "length", "message": {"content": '{"skills":[]}'}}]},
            "provider_incomplete_output",
        ),
        (
            {"choices": [{"finish_reason": "stop", "message": {"content": "   "}}]},
            "provider_empty_output",
        ),
        (
            {"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]},
            "provider_invalid_output",
        ),
        (
            {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"skills":[], "extra":true}'}}
                ]
            },
            "provider_invalid_output",
        ),
    ],
)
async def test_bad_provider_output_is_not_accepted(
    llm_settings: Settings, record: JobRecord, body: dict, expected: str
) -> None:
    extractor = DeepSeekExtractor(
        llm_settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )
    with pytest.raises(ExtractionError, match=expected):
        await extractor.extract(record, SkillCatalog.load())


async def test_provider_error_does_not_echo_body_or_retry(
    llm_settings: Settings, record: JobRecord
) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, text="synthetic-secret")

    extractor = DeepSeekExtractor(llm_settings, transport=httpx.MockTransport(handler))
    with pytest.raises(ExtractionError) as error:
        await extractor.extract(record, SkillCatalog.load())
    assert str(error.value) == "provider_http_error"
    assert calls == 1


async def test_oversized_input_is_rejected_before_network_call(
    llm_settings: Settings, record: JobRecord
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("Oversized input must not reach the provider")

    extractor = DeepSeekExtractor(llm_settings, transport=httpx.MockTransport(handler))
    with pytest.raises(ExtractionError, match="input_too_long"):
        await extractor.extract(
            record.model_copy(update={"description": "x" * 30_001}), SkillCatalog.load()
        )


def test_missing_key_and_model_fail_before_calling_provider(settings: Settings) -> None:
    with pytest.raises(ValueError, match="requires"):
        DeepSeekExtractor(settings)
