import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from app.ingestion.itviec import adapt_itviec, canonical_job_url, fetch_itviec, job_posting
from app.ingestion.policies import local_research_only
from app.ingestion.sources import load_records

URL = "https://itviec.com/it-jobs/synthetic-python-developer-fixture-0001"
POSTING = {
    "@type": "JobPosting",
    "title": "Synthetic Python Developer",
    "description": "<p>Tin giả lập. Python và PostgreSQL.</p>",
    "skills": "Python, PostgreSQL, Fresher Accepted",
    "datePosted": "2026-11-05",
    "hiringOrganization": {"name": "Synthetic Fixture"},
    "jobLocation": [
        {
            "address": {
                "addressLocality": "Not Available",
                "addressRegion": "Hồ Chí Minh",
                "addressCountry": "VN",
            }
        }
    ],
}


def page(posting: dict = POSTING) -> str:
    return '<script type="application/ld+json">' + json.dumps(posting) + "</script>"


def test_parse_single_job_posting_without_navigation_or_breadcrumbs() -> None:
    html = "<h1>Unrelated navigation</h1>" + page({"@type": "BreadcrumbList"}) + page(POSTING)
    assert job_posting(html) == POSTING
    assert job_posting(page({"@graph": [POSTING]})) == POSTING
    with pytest.raises(ValueError, match="unambiguous"):
        job_posting(page(POSTING) + page(POSTING))


def test_adapter_preserves_real_fields_without_inventing_timezone_or_language() -> None:
    record = adapt_itviec({"url": URL + "?tracking=ignored", "posting": POSTING}, datetime.now(UTC))
    assert str(record.source_url) == URL
    assert record.source == "itviec"
    assert record.published_at is None
    assert record.language is None
    assert record.location == "Hồ Chí Minh, VN"
    assert record.description == "Tin giả lập. Python và PostgreSQL."
    assert record.structured_skills == ["Fresher Accepted", "PostgreSQL", "Python"]
    other = adapt_itviec(
        {"url": URL.replace("fixture-", "other-company-"), "posting": POSTING}, datetime.now(UTC)
    )
    assert record.source_job_id != other.source_job_id


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.test/it-jobs/test-0001",
        "https://itviec.com/subscriptions/new",
        "/it-jobs/python",
        "javascript:alert(1)",
    ],
)
def test_discovery_only_accepts_same_origin_job_detail_paths(value: str) -> None:
    assert canonical_job_url(value) is None


async def test_fetch_reuses_snapshot_and_stops_at_access_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeper = AsyncMock()
    monkeypatch.setattr("app.ingestion.itviec.asyncio.sleep", sleeper)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/it-jobs":
            return httpx.Response(
                200, text=f'<a href="{URL}">one</a><a href="{URL[:-4]}0002">two</a>'
            )
        if str(request.url) == URL:
            return httpx.Response(200, text=page())
        return httpx.Response(403)

    path = tmp_path / "snapshot.json"
    transport = httpx.MockTransport(handler)
    await fetch_itviec(path, limit=2, transport=transport)
    await fetch_itviec(path, limit=2, transport=transport)
    records, errors = load_records(path)
    assert len(records) == 1
    assert errors[0]["error"] == "access_stopped"
    assert len(calls) == 4
    snapshot = json.loads(path.read_text())
    assert snapshot["usage_scope"] == "local_research_only"
    assert snapshot["errors"][0]["status"] == 403
    assert all(call.args[0] >= 2 for call in sleeper.await_args_list)


async def test_robots_denial_prevents_access_to_index(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, text="User-agent: *\nDisallow: /\n")

    with pytest.raises(ValueError, match="robots"):
        await fetch_itviec(tmp_path / "denied.json", transport=httpx.MockTransport(handler))
    assert calls == ["/robots.txt"]


async def test_large_sample_is_rejected_before_network_access(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="limited"):
        await fetch_itviec(tmp_path / "large.json", limit=21)


@pytest.mark.parametrize(
    "source,url,expected",
    [
        ("itviec", "https://example.com/job", True),
        ("other", "https://user@www.itviec.com:443/it-jobs/test-0001", True),
        ("other", "https://itviec.com.evil.test/job", False),
        ("other", "https://www.topcv.vn/viec-lam/test", True),
        ("jobtech", "https://arbetsformedlingen.se/job", False),
    ],
)
def test_research_scope_checks_both_source_and_origin(
    source: str, url: str, expected: bool
) -> None:
    assert local_research_only(source, url) is expected
