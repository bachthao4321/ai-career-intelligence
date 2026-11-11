import json

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.ingestion.presentation import country_metadata, title_key, translation_signature
from app.ingestion.translate import trusted_private_target, validate_batch


@pytest.mark.parametrize(
    "source,raw,codes,label",
    [
        ("jobtech", {"workplace_address": {"country": "Sverige"}}, ["SE"], "Thụy Điển"),
        ("jobtech", {"workplace_address": {"country": "Danmark"}}, ["DK"], "Đan Mạch"),
        ("jobtech", {"workplace_address": {"country_code": "199"}}, [], "Chưa rõ quốc gia"),
        ("jobtech", {}, [], "Chưa rõ quốc gia"),
        (
            "itviec",
            {"posting": {"jobLocation": {"address": {"addressCountry": "VN"}}}},
            ["VN"],
            "Việt Nam",
        ),
        ("remotive", {"candidate_required_location": "Worldwide"}, [], "Toàn cầu"),
        (
            "remotive",
            {"candidate_required_location": "USA, Canada, USA timezones"},
            [],
            "Nhiều khu vực",
        ),
        ("remotive", {"candidate_required_location": "Europe"}, [], "Châu Âu"),
        ("remotive", {"candidate_required_location": "USA"}, ["US"], "Hoa Kỳ"),
        ("synthetic", {"title": "Swedish Developer", "location": "Sweden"}, [], "Chưa rõ quốc gia"),
    ],
)
def test_country_uses_explicit_source_provenance(
    source: str, raw: dict, codes: list, label: str
) -> None:
    assert country_metadata(source, raw) == (codes, label)


@pytest.mark.parametrize(
    "items",
    [
        [{"id": "foreign", "en": "Engineer", "vi": "Kỹ sư"}],
        [{"id": "0", "en": "Engineer", "vi": "Kỹ sư"}] * 2,
        [{"id": "0", "en": " ", "vi": "Kỹ sư"}],
        [{"id": "0", "en": "Engineer", "vi": "Kỹ sư", "country": "SE"}],
    ],
)
def test_translation_rejects_foreign_duplicate_blank_or_extra_fields(items: list) -> None:
    with pytest.raises((ValueError, ValidationError)):
        validate_batch(json.dumps({"items": items}), {"0"})


def test_cache_identity_includes_title_model_and_recipe(settings: Settings) -> None:
    assert title_key("Engineer") != title_key("Senior Engineer")
    changed = settings.model_copy(update={"agent_model_name": "different-model"})
    assert translation_signature(settings) != translation_signature(changed)


def test_private_titles_only_go_to_explicit_local_model(settings: Settings) -> None:
    assert not trusted_private_target(settings)
    config = settings.model_copy(
        update={"app_env": "local", "agent_model_base_url": "https://remote.example/v1"}
    )
    assert not trusted_private_target(config)
    config = config.model_copy(
        update={
            "agent_model_base_url": "http://10.20.30.40/v1",
            "model_lan_http_hosts": ["10.20.30.40"],
        }
    )
    assert trusted_private_target(config)
    assert not trusted_private_target(config.model_copy(update={"app_env": "production"}))


def test_reviewed_it_titles_preserve_role_terms_and_technology() -> None:
    from app.ingestion.reviewed_titles import REVIEWED_TITLES

    en, vi = REVIEWED_TITLES["Professional Front-End Developer React & TypeScript - 20593"]
    assert en == "Professional Front-End Developer React & TypeScript - 20593"
    assert all(term in vi for term in ("Frontend", "React", "TypeScript", "20593"))
    assert "giao diện trước" not in vi.lower()
    assert REVIEWED_TITLES["Scrum Master"][1] == "Scrum Master"
    assert "kiến trúc sư" in REVIEWED_TITLES["Senior Software Architect"][1].lower()
