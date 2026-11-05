import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.ingestion.contracts import JobRecord
from app.ingestion.sources import (
    OutOfScopeRecord,
    adapt_jobtech,
    adapt_remotive,
    fetch_jobtech,
    fetch_remotive,
    load_records,
)


def jobtech_row() -> dict:
    return {
        "id": "test-123",
        "headline": "Python Developer",
        "webpage_url": "https://example.com/jobs/test-123",
        "publication_date": "2026-11-05T10:00:00Z",
        "description": {"text": "Python and PostgreSQL"},
        "occupation_field": {"concept_id": "apaJ_2ja_LuF", "label": "Data/IT"},
        "workplace_address": {"municipality": "Stockholm", "country": "Sverige"},
        "employer": {"name": "Synthetic Fixture"},
    }


def test_jobtech_preserves_identity_provenance_and_eligibility() -> None:
    raw = jobtech_row()
    record = adapt_jobtech(raw, datetime.now(UTC))
    assert record.source_job_id == "test-123"
    assert record.location == "Stockholm, Sverige"
    assert record.workplace_type == "unknown"
    assert record.published_at.utcoffset().total_seconds() == 0
    assert record.raw_payload == raw
    assert "arbetsformedlingen.se" in str(record.source_terms_url)


def test_jobtech_checks_category_instead_of_trusting_query_filter() -> None:
    raw = jobtech_row() | {"occupation_field": {"concept_id": "other"}}
    with pytest.raises(OutOfScopeRecord):
        adapt_jobtech(raw, datetime.now(UTC))


def test_timezone_free_source_date_is_preserved_without_inventing_an_offset() -> None:
    raw = jobtech_row() | {"publication_date": "2026-11-05T10:00:00"}
    record = adapt_jobtech(raw, datetime.now(UTC))
    assert record.published_at is None
    assert record.raw_payload["publication_date"] == "2026-11-05T10:00:00"


@pytest.mark.parametrize("invalid_id", [None, True, {}, " "])
def test_source_adapter_rejects_missing_or_non_scalar_ids(invalid_id: object) -> None:
    with pytest.raises(ValueError):
        adapt_jobtech(jobtech_row() | {"id": invalid_id}, datetime.now(UTC))


def test_remotive_keeps_source_link_and_normalizes_html() -> None:
    record = adapt_remotive(
        {
            "id": 1,
            "url": "https://remotive.com/remote-jobs/test-1",
            "title": "Developer",
            "company_name": "Fixture",
            "category": "Software Development",
            "description": "<p>Python &amp; Docker</p>",
            "publication_date": "2026-11-05T00:00:00",
            "candidate_required_location": "Europe",
            "tags": ["Python"],
        },
        datetime.now(UTC),
    )
    assert record.description == "Python & Docker"
    assert record.location == "Europe"
    assert record.workplace_type == "remote"
    assert record.structured_skills == ["Python"]


def test_loader_reports_row_errors_without_importing_invalid_records(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "source": "jobtech",
                "fetched_at": "2026-11-05T00:00:00+00:00",
                "payload": {
                    "hits": [
                        jobtech_row(),
                        {"occupation_field": {"concept_id": "bad"}},
                        jobtech_row() | {"headline": ""},
                    ]
                },
            }
        )
    )
    records, errors = load_records(path)
    assert len(records) == 1
    assert errors == [
        {"row": 1, "error": "outside_it_scope"},
        {"row": 2, "error": "invalid_source_record"},
    ]


def test_local_json_contract_is_source_independent(tmp_path: Path, record: JobRecord) -> None:
    path = tmp_path / "local.json"
    path.write_text(json.dumps([record.model_dump(mode="json")]))
    records, errors = load_records(path)
    assert errors == []
    assert records == [record]


async def test_fetch_is_bounded_and_daily_snapshot_is_reused(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        limit = int(request.url.params["limit"])
        offset = int(request.url.params["offset"])
        return httpx.Response(
            200, json={"hits": [jobtech_row() | {"id": str(offset + i)} for i in range(limit)]}
        )

    path = tmp_path / "snapshot.json"
    transport = httpx.MockTransport(handler)
    await fetch_jobtech(path, limit=150, transport=transport)
    await fetch_jobtech(path, limit=150, transport=transport)
    records, errors = load_records(path)
    assert len(calls) == 2
    assert [(row["limit"], row["offset"]) for row in calls] == [("100", "0"), ("50", "100")]
    assert len(records) == 150
    assert errors == []


async def test_failed_fetch_does_not_create_a_snapshot_or_retry(tmp_path: Path) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429)

    path = tmp_path / "snapshot.json"
    with pytest.raises(httpx.HTTPStatusError):
        await fetch_remotive(path, transport=httpx.MockTransport(handler))
    assert calls == 1
    assert not path.exists()
