"""Fetch a bounded set of employer job boards into an ignored local snapshot.

Run from the repository root: python -m scripts.fetch_vietnam_employers
These records are for local research; public redistribution has not been established.
"""

import html
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from app.ingestion.sources import html_to_text

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
URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{board}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{board}",
}
TERMS = {
    "greenhouse": "https://job-boards.greenhouse.io/{board}",
    "lever": "https://jobs.lever.co/{board}",
    "ashby": "https://jobs.ashbyhq.com/{board}",
}


def normalize(provider: str, row: dict) -> tuple[str, str, str, str | None]:
    """Return title, location, plain description and public URL for one posting."""
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


def main() -> None:
    fetched = datetime.now(UTC).isoformat()
    records = []
    for provider, board, company in BOARDS:
        try:
            with urlopen(
                Request(
                    URLS[provider].format(board=board),
                    headers={"User-Agent": "CareerLens local research/1.0"},
                ),
                timeout=25,
            ) as response:
                data = json.load(response)
            jobs = data.get("jobs", []) if isinstance(data, dict) else data
            added = 0
            for row in jobs:
                if provider == "ashby" and not row.get("isListed", True):
                    continue
                title, location, content, source_url = normalize(provider, row)
                if not title or not VIETNAM.search(location) or not IT_ROLE.search(title):
                    continue
                if NOT_IT.search(title):
                    continue
                if not content.strip() or not source_url:
                    continue
                records.append(
                    {
                        "source": "employer_ats",
                        "source_job_id": f"{provider}:{board}:{row['id']}",
                        "source_url": source_url,
                        "source_terms_url": TERMS[provider].format(board=board),
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
            print(f"{company}: {added} tin IT tại Việt Nam")
        except (OSError, ValueError, KeyError) as exc:
            print(f"{company}: không lấy được ({type(exc).__name__})")
        time.sleep(1)
    path = Path("data/raw") / f"employer-ats-vietnam-{datetime.now(UTC).date()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(records, stream, ensure_ascii=False)
    print(f"Snapshot: {path}; tổng {len(records)} tin")


if __name__ == "__main__":
    main()
