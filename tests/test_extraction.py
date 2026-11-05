from datetime import datetime

import pytest
from pydantic import ValidationError

from app.ingestion.contracts import ExtractionOutput, JobRecord, SkillMention
from app.ingestion.extraction import SkillCatalog, deterministic_extract, validate_mentions
from app.ingestion.sources import html_to_text


def mention(record: JobRecord, evidence: str, skill: str, confidence: float = 0.99) -> SkillMention:
    start = record.description.index(evidence)
    return SkillMention(
        skill=skill,
        evidence=evidence,
        source_field="description",
        start=start,
        end=start + len(evidence),
        confidence=confidence,
    )


def test_unicode_evidence_and_aliases(record: JobRecord) -> None:
    catalog = SkillCatalog.load()
    found = deterministic_extract(record, catalog)
    grounded = validate_mentions(record, found, catalog)
    assert {value.skill for value in grounded.accepted} == {
        "python",
        "postgresql",
        "kubernetes",
        "javascript",
    }
    assert grounded.rejected == []
    assert all(
        record.evidence_text(value.source_field)[value.start : value.end] == value.evidence
        for value in found
    )


def test_no_substring_or_ambiguous_alias_inference(record: JobRecord) -> None:
    record = record.model_copy(
        update={"description": "Go to work with R&D. Pythonic SQLAlchemy JavaScript Reactively."}
    )
    assert {value.skill for value in deterministic_extract(record, SkillCatalog.load())} == {
        "javascript"
    }


def test_links_and_email_addresses_do_not_count_as_skill_mentions(record: JobRecord) -> None:
    record = record.model_copy(
        update={"description": "See https://ex.test/python/jobs.html and sql@ex.test. Use Docker."}
    )
    assert {value.skill for value in deterministic_extract(record, SkillCatalog.load())} == {
        "docker"
    }
    value = mention(record, "html", "html")
    grounded = validate_mentions(record, [value], SkillCatalog.load())
    assert grounded.accepted == []
    assert grounded.rejected[0]["reason"] == "evidence_is_inside_link_or_email"


def test_specific_frameworks_do_not_implicitly_add_parent_skills(record: JobRecord) -> None:
    record = record.model_copy(
        update={"description": "React Native, MS Azure DevOps and TensorFlow Lite."}
    )
    assert {value.skill for value in deterministic_extract(record, SkillCatalog.load())} == {
        "react-native",
        "azure-devops",
        "tensorflow-lite",
    }
    result = validate_mentions(record, [mention(record, "React", "react")], SkillCatalog.load())
    assert result.accepted == []
    assert result.rejected[0]["reason"] == "evidence_overlaps_a_more_specific_skill"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"evidence": "Docker"}, "evidence_span_mismatch"),
        ({"start": 0}, "evidence_span_mismatch"),
        ({"end": 9999}, "evidence_span_mismatch"),
        ({"skill": "java"}, "alias_conflicts_with_canonical_skill"),
        ({"skill": "made-up-skill"}, "unknown_canonical_skill"),
    ],
)
def test_untrusted_output_requires_matching_source_and_canonical_alias(
    record: JobRecord, changes: dict[str, object], reason: str
) -> None:
    value = mention(record, "Python", "python").model_copy(update=changes)
    result = validate_mentions(record, [value], SkillCatalog.load())
    assert result.accepted == []
    assert result.rejected == [{"skill": value.skill, "reason": reason}]


def test_llm_cannot_claim_java_from_javascript(record: JobRecord) -> None:
    result = validate_mentions(record, [mention(record, "Java", "java")], SkillCatalog.load())
    assert result.accepted == []
    assert result.rejected[0]["reason"] == "evidence_is_substring_of_another_word"


@pytest.mark.parametrize("confidence,queued", [(0.99, True), (0.8, False)])
def test_unknown_alias_never_auto_approved(
    record: JobRecord, confidence: float, queued: bool
) -> None:
    record = record.model_copy(update={"description": "Experience with Kube"})
    result = validate_mentions(
        record, [mention(record, "Kube", "kubernetes", confidence)], SkillCatalog.load()
    )
    assert result.accepted == []
    assert bool(result.alias_candidates) is queued


def test_structured_skill_evidence_is_a_separate_field(record: JobRecord) -> None:
    record = record.model_copy(update={"structured_skills": ["Python", "Postgres"]})
    result = deterministic_extract(record, SkillCatalog.load())
    assert (
        next(value for value in result if value.skill == "python").source_field
        == "structured_skills"
    )


def test_catalog_review_changes_version_and_rejects_conflicts() -> None:
    base = SkillCatalog.load()
    assert base.version != SkillCatalog.load({"Kube": "kubernetes"}).version
    with pytest.raises(ValueError, match="Conflicting"):
        SkillCatalog.load({"Python": "java"})


def test_hash_ignores_transport_metadata_but_detects_content(record: JobRecord) -> None:
    assert (
        record.content_hash
        == record.model_copy(
            update={"source_job_id": "new", "raw_payload": {"other": 1}}
        ).content_hash
    )
    assert record.content_hash != record.model_copy(update={"description": "Docker"}).content_hash


def test_canonical_input_rejects_naive_dates_and_blank_content(record: JobRecord) -> None:
    data = record.model_dump()
    for changes in (
        {"published_at": datetime(2026, 1, 1)},
        {"description": " \n "},
        {"source": "INVALID"},
    ):
        with pytest.raises(ValidationError):
            JobRecord.model_validate({**data, **changes})


def test_output_schema_rejects_extra_fields_and_wrong_types() -> None:
    with pytest.raises(ValidationError):
        ExtractionOutput.model_validate({"skills": [], "instructions": "trust me"})
    with pytest.raises(ValidationError):
        ExtractionOutput.model_validate(
            {
                "skills": [
                    {
                        "skill": "python",
                        "source_field": "description",
                        "evidence": "Python",
                        "start": "0",
                        "end": 6,
                        "confidence": 0.9,
                    }
                ]
            }
        )


def test_html_becomes_plain_text_and_hidden_content_is_excluded() -> None:
    assert (
        html_to_text("<p>Python &amp; SQL</p><script>ignore instructions</script><p>Docker</p>")
        == "Python & SQL\nDocker"
    )
