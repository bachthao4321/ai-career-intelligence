import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

from app.ingestion.contracts import JobRecord, normalize_text

REMOTIVE_ENDPOINT = "https://remotive.com/api/remote-jobs"
REMOTIVE_TERMS = "https://github.com/remotive-com/remote-jobs-api#terms-of-services"
JOBTECH_ENDPOINT = "https://jobsearch.api.jobtechdev.se/search"
JOBTECH_TERMS = "https://data.arbetsformedlingen.se/dataservice/jobsearch/"


class OutOfScopeRecord(ValueError):
    pass


def parse_publication_time(value: str) -> datetime | None:
    published = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # An offset-free source value does not prove an absolute instant. Keep it in raw data.
    return published if published.tzinfo is not None else None


def source_identity(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, str | int) or not str(value).strip():
        raise ValueError("A stable source ID is required")
    return str(value).strip()


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden_depth += 1
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "ul", "ol"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden_depth = max(0, self.hidden_depth - 1)
        if tag in {"p", "div", "li", "h1", "h2", "h3", "ul", "ol"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def html_to_text(value: str) -> str:
    parser = _TextParser()
    parser.feed(value)
    return normalize_text("".join(parser.parts))


def adapt_remotive(job: dict[str, Any], fetched_at: datetime) -> JobRecord:
    if job.get("category") not in {
        "Software Development",
        "DevOps / Sysadmin",
        "Data",
        "QA",
        "Information Technology",
        "Artificial Intelligence",
    }:
        raise OutOfScopeRecord("Non-IT Remotive category")
    published = parse_publication_time(str(job["publication_date"]))
    return JobRecord(
        source="remotive",
        source_job_id=source_identity(job["id"]),
        source_url=job["url"],
        source_terms_url=REMOTIVE_TERMS,
        title=job["title"],
        company=job.get("company_name"),
        location=job.get("candidate_required_location"),
        workplace_type="remote",
        description=html_to_text(job["description"]),
        structured_skills=job.get("tags") or [],
        published_at=published,
        fetched_at=fetched_at,
        raw_payload=job,
    )


def adapt_jobtech(job: dict[str, Any], fetched_at: datetime) -> JobRecord:
    if (job.get("occupation_field") or {}).get("concept_id") != "apaJ_2ja_LuF":
        raise OutOfScopeRecord("Non-IT JobTech occupation field")
    published = parse_publication_time(str(job["publication_date"]))
    address = job.get("workplace_address") or {}
    location = (
        ", ".join(
            dict.fromkeys(
                value
                for key in ("municipality", "region", "country")
                if (value := address.get(key))
            )
        )
        or None
    )
    return JobRecord(
        source="jobtech",
        source_job_id=source_identity(job["id"]),
        source_url=job["webpage_url"],
        source_terms_url=JOBTECH_TERMS,
        title=job["headline"],
        company=(job.get("employer") or {}).get("name"),
        location=location,
        description=job["description"]["text"],
        published_at=published,
        fetched_at=fetched_at,
        raw_payload=job,
    )


async def fetch_jobtech(
    destination: Path, *, limit: int = 150, transport: httpx.AsyncBaseTransport | None = None
) -> Path:
    if not 1 <= limit <= 200:
        raise ValueError("Fetch limit must be between 1 and 200")
    if await asyncio.to_thread(destination.exists):
        return destination
    jobs: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        timeout=60, follow_redirects=False, trust_env=False, transport=transport
    ) as client:
        while len(jobs) < limit:
            params: dict[str, str | int] = {
                "occupation-field": "apaJ_2ja_LuF",
                "limit": min(100, limit - len(jobs)),
                "offset": len(jobs),
            }
            response = await client.get(JOBTECH_ENDPOINT, params=params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("hits"), list):
                raise ValueError("Unexpected JobTech response shape")
            requests.append(params)
            rows = payload["hits"]
            if not rows:
                break
            jobs.extend(rows)
    snapshot = {
        "source": "jobtech",
        "endpoint": JOBTECH_ENDPOINT,
        "terms_url": JOBTECH_TERMS,
        "license": "CC0",
        "fetched_at": datetime.now(UTC).isoformat(),
        "requests": requests,
        "payload": {"hits": jobs[:limit]},
    }
    await asyncio.to_thread(_write_snapshot, destination, snapshot)
    return destination


async def fetch_remotive(
    destination: Path, *, limit: int = 200, transport: httpx.AsyncBaseTransport | None = None
) -> Path:
    if not 1 <= limit <= 200:
        raise ValueError("Fetch limit must be between 1 and 200")
    # Reuse the daily snapshot; never poll a public source from the test suite.
    if await asyncio.to_thread(destination.exists):
        return destination
    async with httpx.AsyncClient(
        timeout=60, follow_redirects=False, trust_env=False, transport=transport
    ) as client:
        response = await client.get(
            REMOTIVE_ENDPOINT,
            params={"category": "software-dev", "limit": limit},
            headers={"User-Agent": "AI-Career-Intelligence/0.1 (portfolio; public API)"},
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ValueError("Unexpected Remotive response shape")
    snapshot = {
        "source": "remotive",
        "endpoint": REMOTIVE_ENDPOINT,
        "terms_url": REMOTIVE_TERMS,
        "fetched_at": datetime.now(UTC).isoformat(),
        "request": {"category": "software-dev", "limit": limit},
        "payload": payload,
    }
    await asyncio.to_thread(_write_snapshot, destination, snapshot)
    return destination


def _write_snapshot(destination: Path, snapshot: dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental replacement of an existing source snapshot.
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(snapshot, stream, ensure_ascii=False)


def load_records(path: Path) -> tuple[list[JobRecord], list[dict[str, Any]]]:
    if path.stat().st_size > 25_000_000:
        raise ValueError("Import files must not exceed 25 MB")
    data = json.loads(path.read_text(encoding="utf-8"))
    errors: list[dict[str, Any]] = []
    records: list[JobRecord] = []
    adapter: Callable[[Any], JobRecord]
    if isinstance(data, dict) and data.get("source") == "remotive":
        fetched = datetime.fromisoformat(data["fetched_at"])
        rows = data["payload"]["jobs"]
        adapter = partial(adapt_remotive, fetched_at=fetched)
    elif isinstance(data, dict) and data.get("source") == "jobtech":
        fetched = datetime.fromisoformat(data["fetched_at"])
        rows = data["payload"]["hits"]
        adapter = partial(adapt_jobtech, fetched_at=fetched)
    elif isinstance(data, list):
        rows = data
        adapter = JobRecord.model_validate
    elif isinstance(data, dict) and data.get("source") == "itviec":
        from app.ingestion.itviec import adapt_itviec

        fetched = datetime.fromisoformat(data["fetched_at"])
        rows = data["payload"]["jobs"]
        adapter = partial(adapt_itviec, fetched_at=fetched)
        errors.extend(data.get("errors", []))
    else:
        raise ValueError(
            "Expected a supported source snapshot or JSON array of canonical JobRecord objects"
        )
    if len(rows) > 5000:
        raise ValueError("At most 5000 records may be imported per file")
    for index, row in enumerate(rows):
        try:
            records.append(adapter(row))
        except OutOfScopeRecord:
            errors.append({"row": index, "error": "outside_it_scope"})
        except (ValueError, KeyError, TypeError, AttributeError):
            # Keep errors actionable without copying untrusted source text or credentials to logs.
            errors.append({"row": index, "error": "invalid_source_record"})
    return records, errors
