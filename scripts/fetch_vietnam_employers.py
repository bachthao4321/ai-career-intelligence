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
)
VIETNAM = re.compile(r"\b(vietnam|viet nam|ho chi minh|hanoi|ha noi|hà nội|hồ chí minh)\b", re.I)
IT_ROLE = re.compile(
    r"\b(engineer|developer|devops|software|data|machine learning|ai|automation|"
    r"quality assurance|ux/ui|technical|cybersecurity)\b",
    re.I,
)


def main() -> None:
    fetched = datetime.now(UTC).isoformat()
    records = []
    for provider, board, company in BOARDS:
        url = (
            f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
            if provider == "greenhouse"
            else f"https://api.lever.co/v0/postings/{board}?mode=json"
        )
        try:
            with urlopen(
                Request(url, headers={"User-Agent": "CareerLens local research/1.0"}), timeout=25
            ) as response:
                data = json.load(response)
            jobs = data.get("jobs", []) if isinstance(data, dict) else data
            added = 0
            for row in jobs:
                title = row.get("title") if provider == "greenhouse" else row.get("text")
                location = (
                    (row.get("location") or {}).get("name")
                    if provider == "greenhouse"
                    else (row.get("categories") or {}).get("location")
                ) or ""
                if not title or not VIETNAM.search(location) or not IT_ROLE.search(title):
                    continue
                content = (
                    row.get("content", "")
                    if provider == "greenhouse"
                    else row.get("descriptionPlain", "")
                )
                if provider == "lever":
                    content += (
                        "\n"
                        + "\n".join(
                            (section.get("text") or "")
                            + "\n"
                            + html_to_text(section.get("content") or "")
                            for section in row.get("lists", [])
                        )
                        + "\n"
                        + (row.get("additionalPlain") or "")
                    )
                else:
                    content = html_to_text(html.unescape(content))
                if not content.strip():
                    continue
                source_url = (
                    row.get("absolute_url") if provider == "greenhouse" else row.get("hostedUrl")
                )
                records.append(
                    {
                        "source": "employer_ats",
                        "source_job_id": f"{provider}:{board}:{row['id']}",
                        "source_url": source_url,
                        "source_terms_url": f"https://job-boards.greenhouse.io/{board}"
                        if provider == "greenhouse"
                        else f"https://jobs.lever.co/{board}",
                        "title": title,
                        "company": company,
                        "location": location,
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
