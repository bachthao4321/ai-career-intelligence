import asyncio
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


async def test_jobtech_continues_from_offset_and_reuses_window_snapshot(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        calls.append((offset, limit))
        return httpx.Response(
            200, json={"hits": [jobtech_row() | {"id": str(offset + i)} for i in range(limit)]}
        )

    path = tmp_path / "next-window.json"
    transport = httpx.MockTransport(handler)
    await fetch_jobtech(path, offset=200, limit=125, transport=transport)
    await fetch_jobtech(path, offset=200, limit=125, transport=transport)
    records, errors = load_records(path)
    assert calls == [(200, 100), (300, 25)]
    assert [record.source_job_id for record in records] == [str(i) for i in range(200, 325)]
    assert errors == []


@pytest.mark.parametrize("offset,limit", [(-1, 1), (2000, 1), (1900, 200)])
async def test_jobtech_rejects_out_of_range_window_before_fetch(
    tmp_path: Path, offset: int, limit: int
) -> None:
    path = tmp_path / "invalid.json"
    with pytest.raises(ValueError, match="window"):
        await fetch_jobtech(path, offset=offset, limit=limit)
    assert not path.exists()


async def test_cli_uses_separate_snapshot_paths_for_jobtech_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.ingestion import cli

    monkeypatch.chdir(tmp_path)
    seen = []

    async def fetch(path: Path, *, limit: int, offset: int) -> Path:
        seen.append((path, offset))
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(
            path.write_text,
            json.dumps(
                {
                    "source": "jobtech",
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "payload": {"hits": [jobtech_row()]},
                }
            ),
        )
        return path

    monkeypatch.setattr(cli, "fetch_jobtech", fetch)
    for offset in [0, 200]:
        result = await cli.run(
            cli.parser().parse_args(["fetch", "jobtech", "--offset", str(offset)])
        )
        assert result["valid_jobs"] == 1
    assert seen[0][0] != seen[1][0]
    assert seen[1][0].name.endswith("-offset-200.json")


async def test_cli_rejects_offsets_for_sources_without_pagination() -> None:
    from app.ingestion.cli import parser, run

    with pytest.raises(ValueError, match="Only JobTech"):
        await run(parser().parse_args(["fetch", "remotive", "--offset", "200"]))
