"""Employer career pages with Vietnam offices: public ATS boards (Greenhouse, Lever, Ashby)
and two company sites (CMC TS, VNG). Records are for local research; public redistribution
has not been established. No access controls are bypassed."""

import html
import json
import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.request import Request, urlopen

from app.ingestion.sources import html_to_text

Record = dict[str, Any]
AGENT = {"User-Agent": "CareerLens local research/1.0"}

BOARDS = (
    ("greenhouse", "temus", "Temus"),
    ("greenhouse", "rightship", "RightShip"),
    ("lever", "Clearer", "Clearer.io"),
    ("lever", "keyloop", "Keyloop"),
    ("greenhouse", "axon", "Axon"),
    ("greenhouse", "thoughtworks", "Thoughtworks"),
    ("greenhouse", "agoda", "Agoda"),
    ("greenhouse", "binance", "Binance"),
    ("lever", "ninjavan", "Ninja Van"),
    ("lever", "lalamove", "Lalamove"),
    ("ashby", "skymavis", "Sky Mavis"),
    ("ashby", "airwallex", "Airwallex"),
    ("ashby", "hrs", "HRS"),
)
BOARD_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{board}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{board}",
}
BOARD_TERMS = {
    "greenhouse": "https://job-boards.greenhouse.io/{board}",
    "lever": "https://jobs.lever.co/{board}",
    "ashby": "https://jobs.ashbyhq.com/{board}",
}
VIETNAM = re.compile(
    r"\b(vietnam|viet nam|việt nam|ho chi minh|hcmc|saigon|hanoi|ha noi|hà nội|hồ chí minh|"
    r"da nang|đà nẵng)\b",
    re.I,
)
IT_ROLE = re.compile(
    r"\b(engineer|developer|devops|software|data|machine learning|ai|automation|"
    r"quality assurance|qa|ux/ui|technical|cybersecurity|security|sre|architect|"
    r"frontend|backend|full.?stack|android|ios|tester)\b",
    re.I,
)
# Titles that match an IT word but are not engineering roles ("Product Manager, Mobile").
NOT_IT = re.compile(r"\b(product manager|compliance|kyc|kyb|marketing|sales|recruit)", re.I)
CMC_ROLE = re.compile(
    r"data|engineer|developer|full.stack|technical.leader|architect|administrator|"
    r"trien.khai.phan.mem|tester|it.support|helpdesk|software|devops|\bai\b",
    re.I,
)
VNG_ROLE = re.compile(
    r"engineer|developer|software|devops|data (analyst|scientist|analytics|engineer)|\bai\b", re.I
)


def get(url: str) -> str:
    with urlopen(Request(url, headers=AGENT), timeout=25) as response:
        body: bytes = response.read()
    return body.decode("utf-8", "replace")


def normalize_posting(provider: str, row: Record) -> tuple[str, str, str, str | None]:
    """Return title, location, plain description and public URL for one ATS posting."""
    if provider == "greenhouse":
        return (
            row.get("title") or "",
            (row.get("location") or {}).get("name") or "",
            html_to_text(html.unescape(row.get("content") or "")),
            row.get("absolute_url"),
        )
    if provider == "lever":
        sections = "\n".join(
            (section.get("text") or "") + "\n" + html_to_text(section.get("content") or "")
            for section in row.get("lists", [])
        )
        content = "\n".join(
            (row.get("descriptionPlain") or "", sections, row.get("additionalPlain") or "")
        )
        return (
            row.get("text") or "",
            (row.get("categories") or {}).get("location") or "",
            content,
            row.get("hostedUrl"),
        )
    locations = [row.get("location") or ""] + [
        item.get("location") or "" for item in row.get("secondaryLocations") or []
    ]
    content = row.get("descriptionPlain") or html_to_text(row.get("descriptionHtml") or "")
    return row.get("title") or "", " / ".join(filter(None, locations)), content, row.get("jobUrl")


def ats_boards(fetched: str, counts: dict[str, int | str]) -> list[Record]:
    records: list[Record] = []
    for provider, board, company in BOARDS:
        try:
            data = json.loads(get(BOARD_URLS[provider].format(board=board)))
            jobs = data.get("jobs", []) if isinstance(data, dict) else data
            added = 0
            for row in jobs:
                if provider == "ashby" and not row.get("isListed", True):
                    continue
                title, location, content, source_url = normalize_posting(provider, row)
                if not title or not VIETNAM.search(location) or not IT_ROLE.search(title):
                    continue
                if NOT_IT.search(title) or not content.strip() or not source_url:
                    continue
                records.append(
                    {
                        "source": "employer_ats",
                        "source_job_id": f"{provider}:{board}:{row['id']}",
                        "source_url": source_url,
                        "source_terms_url": BOARD_TERMS[provider].format(board=board),
                        "title": title,
                        "company": company,
                        "location": location[:500],
                        "description": content,
                        "fetched_at": fetched,
                        "raw_payload": {
                            "provider": provider,
                            "board": board,
                            "verified_country": "VN",
                            "source_record": row,
                        },
                    }
                )
                added += 1
            counts[company] = added
        except (OSError, ValueError, KeyError) as exc:
            counts[company] = type(exc).__name__
        time.sleep(1)
    return records


class CmcDetail(HTMLParser):
    """Collects the job title and article text from a CMC TS job page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.collect = False
        self.in_title = False
        self.title_parts: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if tag == "h1" and "detail-title" in classes:
            self.in_title = True
        if tag == "div" and "detail-article" in classes:
            self.collect = True
            self.depth = 1
        elif self.collect and tag == "div":
            self.depth += 1
        if self.collect and tag in {"p", "li", "br", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1":
            self.in_title = False
        if self.collect and tag == "div":
            self.depth -= 1
            if not self.depth:
                self.collect = False
        if self.collect and tag in {"p", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if self.collect:
            self.parts.append(data)


def cmc_jobs(fetched: str) -> list[Record]:
    base = "https://careers.cmcts.com.vn"
    rows: list[Record] = []
    links = dict.fromkeys(re.findall(r"/job/[a-z0-9.-]+-\d+", get(base + "/jobs")))
    for path in links:
        if not CMC_ROLE.search(path):
            continue
        try:
            row = cmc_job(base, path, fetched)
        except (OSError, ValueError):
            row = None
        if row is not None:
            rows.append(row)
        time.sleep(0.5)
    return rows


def cmc_job(base: str, path: str, fetched: str) -> Record | None:
    page = get(base + path)
    detail = CmcDetail()
    detail.feed(page)
    title = "".join(detail.title_parts).strip()
    description = "".join(detail.parts).strip()
    deadline = re.search(r'"validThrough"\s*:\s*"(\d{4}-\d{2}-\d{2})"', page)
    if not title or not description or not deadline:
        return None
    if date.fromisoformat(deadline.group(1)) < date.today():
        return None
    marker = title.upper()
    location = (
        "Hồ Chí Minh, Việt Nam"
        if "[HCM]" in marker
        else "Hà Nội, Việt Nam"
        if "[HN]" in marker
        else None
    )
    return {
        "source": "employer_ats",
        "source_job_id": "cmcts:" + path.rsplit("-", 1)[-1],
        "source_url": base + path,
        "source_terms_url": base + "/",
        "title": title,
        "company": "CMC Technology & Solution",
        "location": location,
        "description": description,
        "fetched_at": fetched,
        "raw_payload": {
            "provider": "CMC TS",
            "verified_country": "VN",
            "application_deadline": deadline.group(1),
        },
    }


def vng_jobs(fetched: str) -> list[Record]:
    rows: list[Record] = []
    for page_number in range(1, 8):
        page = get(f"https://career.vng.com.vn/vi/tim-kiem-viec-lam?page={page_number}")
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page)
        if not match:
            break
        data = json.loads(match.group(1))["props"]["pageProps"]
        for job in data["jobs"]:
            if job.get("job_family") not in {"Tech", "Data"} and not VNG_ROLE.search(
                job.get("title", "")
            ):
                continue
            location = job.get("location") or ""
            if not any(city in location.casefold() for city in ["hồ chí minh", "hà nội"]):
                continue
            description = html_to_text(job.get("description") or "")
            if not description:
                continue
            rows.append(
                {
                    "source": "employer_ats",
                    "source_job_id": "vng:" + str(job["job_id"]),
                    "source_url": "https://career.vng.com.vn/vi/tim-kiem-viec-lam/chi-tiet/"
                    + job["slug"],
                    "source_terms_url": "https://career.vng.com.vn/",
                    "title": job["title"],
                    "company": "VNG",
                    "location": location,
                    "description": description,
                    "fetched_at": fetched,
                    "raw_payload": {
                        "provider": "VNG Careers",
                        "verified_country": "VN",
                        "source_record": job,
                    },
                }
            )
        if page_number >= int(data.get("pages", page_number)):
            break
        time.sleep(0.5)
    return rows


def fetch_employers() -> tuple[list[Record], dict[str, int | str]]:
    """Fetch every employer source; one failing site only zeroes its own count."""
    fetched = datetime.now(UTC).isoformat()
    counts: dict[str, int | str] = {}
    records = ats_boards(fetched, counts)
    sites: dict[str, Callable[[str], list[Record]]] = {"CMC TS": cmc_jobs, "VNG": vng_jobs}
    for name, fetch in sites.items():
        try:
            rows = fetch(fetched)
            counts[name] = len(rows)
            records.extend(rows)
        except (OSError, ValueError, KeyError) as exc:
            counts[name] = type(exc).__name__
    return records, counts
