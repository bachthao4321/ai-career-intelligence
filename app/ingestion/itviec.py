import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.ingestion.contracts import JobRecord
from app.ingestion.sources import _write_snapshot, html_to_text, parse_publication_time

ITVIEC_INDEX = "https://itviec.com/it-jobs"
ITVIEC_TERMS = "https://itviec.com/blog/terms-and-conditions/"
USER_AGENT = "CareerIntelligenceResearch/0.1"


def canonical_job_url(value: str) -> str | None:
    parsed = urlsplit(urljoin(ITVIEC_INDEX, value))
    if parsed.scheme != "https" or parsed.netloc.lower() != "itviec.com":
        return None
    if not re.fullmatch(r"/it-jobs/[^/]+-\d{4,}", parsed.path):
        return None
    return urlunsplit(("https", "itviec.com", parsed.path, "", ""))


class JobPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.blocks: list[str] = []
        self._capture = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a" and (value := attributes.get("href")):
            if url := canonical_job_url(value):
                self.links.append(url)
        if tag == "script" and attributes.get("type") == "application/ld+json":
            self._capture = True
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capture:
            self.blocks.append("".join(self._buffer))
            self._capture = False


def job_posting(html: str) -> dict[str, Any]:
    if len(html) > 5_000_000:
        raise ValueError("Page exceeds the parsing limit")
    parser = JobPageParser()
    parser.feed(html)
    postings: list[dict[str, Any]] = []
    for block in parser.blocks[:20]:
        try:
            data = json.loads(block)
        except ValueError:
            continue
        nodes = data[:120] if isinstance(data, list) else [data]
        for node in nodes:
            if isinstance(node, dict) and isinstance(node.get("@graph"), list):
                nodes = nodes + node["@graph"][:100]
        # Only a bounded single level of @graph is accepted.
        for node in nodes[:120]:
            if not isinstance(node, dict):
                continue
            kind = node.get("@type")
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                postings.append(node)
    if len(postings) != 1:
        raise ValueError("Expected exactly one unambiguous JobPosting")
    return postings[0]


def adapt_itviec(row: dict[str, Any], fetched_at: datetime) -> JobRecord:
    url = canonical_job_url(row["url"])
    if url is None:
        raise ValueError("Invalid ITviec job URL")
    posting = row["posting"]
    addresses = posting.get("jobLocation") or []
    if isinstance(addresses, dict):
        addresses = [addresses]
    locations: list[str] = []
    for place in addresses[:20]:
        address = place.get("address") or {}
        parts = [address.get(key) for key in ("addressLocality", "addressRegion", "addressCountry")]
        location = ", ".join(
            dict.fromkeys(str(part).strip() for part in parts if part and part != "Not Available")
        )
        if location:
            locations.append(location)
    skills = posting.get("skills") or []
    if isinstance(skills, str):
        skills = [value.strip() for value in skills.split(",") if value.strip()]
    return JobRecord(
        source="itviec",
        # The short numeric URL suffix is not a documented globally unique job ID.
        source_job_id=hashlib.sha256(url.encode()).hexdigest(),
        source_url=url,
        source_terms_url=ITVIEC_TERMS,
        title=posting["title"],
        company=(posting.get("hiringOrganization") or {}).get("name"),
        location="; ".join(dict.fromkeys(locations)) or None,
        workplace_type="remote" if posting.get("jobLocationType") == "TELECOMMUTE" else "unknown",
        description=html_to_text(posting["description"]),
        structured_skills=skills,
        published_at=parse_publication_time(posting["datePosted"])
        if posting.get("datePosted")
        else None,
        fetched_at=fetched_at,
        raw_payload=row,
    )


async def fetch_itviec(
    destination: Path, *, limit: int = 10, transport: httpx.AsyncBaseTransport | None = None
) -> Path:
    if not 1 <= limit <= 20:
        raise ValueError("Personal-research sample is limited to 1–20 listings")
    if await asyncio.to_thread(destination.exists):
        return destination
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str | int]] = []
    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=False,
        trust_env=False,
        transport=transport,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        robots_response = await client.get("https://itviec.com/robots.txt")
        robots_response.raise_for_status()
        robots = RobotFileParser()
        robots.parse(robots_response.text.splitlines())
        if not robots.can_fetch(USER_AGENT, ITVIEC_INDEX):
            raise ValueError("Index disallowed by robots.txt")
        delay = max(2.0, float(robots.crawl_delay(USER_AGENT) or robots.crawl_delay("*") or 0))
        if delay > 30:
            raise ValueError("Crawl delay requires a separate scheduled workflow")
        await asyncio.sleep(delay)
        index = await client.get(ITVIEC_INDEX)
        index.raise_for_status()
        parser = JobPageParser()
        parser.feed(index.text)
        urls = list(dict.fromkeys(parser.links))[:limit]
        if not urls:
            raise ValueError("No public job links found")
        for url in urls:
            if not robots.can_fetch(USER_AGENT, url):
                errors.append({"url": url, "error": "robots_disallowed"})
                continue
            await asyncio.sleep(delay)
            response = await client.get(url)
            if response.status_code in {401, 403, 429}:
                errors.append(
                    {"url": url, "error": "access_stopped", "status": response.status_code}
                )
                break
            if response.status_code != 200:
                errors.append({"url": url, "error": "http_error", "status": response.status_code})
                continue
            try:
                posting = job_posting(response.text)
                row = {"url": url, "posting": posting, "usage_scope": "local_research_only"}
                adapt_itviec(row, datetime.now(UTC))
            except (ValueError, TypeError, KeyError, AttributeError):
                errors.append({"url": url, "error": "invalid_job_posting"})
                continue
            rows.append(row)
    snapshot = {
        "source": "itviec",
        "endpoint": ITVIEC_INDEX,
        "terms_url": ITVIEC_TERMS,
        "fetched_at": datetime.now(UTC).isoformat(),
        "usage_scope": "local_research_only",
        "request_limit": limit,
        "errors": errors,
        "payload": {"jobs": rows},
    }
    await asyncio.to_thread(_write_snapshot, destination, snapshot)
    return destination
