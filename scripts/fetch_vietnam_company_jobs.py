"""Fetch public company career pages for local research, without bypassing access controls."""

import json
import re
import time
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

from app.ingestion.sources import html_to_text

AGENT = {"User-Agent": "CareerLensResearch/1.0 (local research)"}
ROLE = re.compile(
    r"data|engineer|developer|full.stack|technical.leader|architect|administrator|trien.khai.phan.mem|tester|it.support|helpdesk|software|devops|\bai\b",
    re.I,
)
VNG_ROLE = re.compile(
    r"engineer|developer|software|devops|data (analyst|scientist|analytics|engineer)|\bai\b", re.I
)


def get(url):
    with urlopen(Request(url, headers=AGENT), timeout=25) as response:
        return response.read().decode("utf-8", "replace")


class Detail(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.collect = False
        self.title = False
        self.title_parts = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "h1" and "detail-title" in attrs.get("class", ""):
            self.title = True
        if tag == "div" and "detail-article" in attrs.get("class", "").split():
            self.collect = True
            self.depth = 1
        elif self.collect and tag == "div":
            self.depth += 1
        if self.collect and tag in {"p", "li", "br", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "h1":
            self.title = False
        if self.collect and tag == "div":
            self.depth -= 1
            if not self.depth:
                self.collect = False
        if self.collect and tag in {"p", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.title:
            self.title_parts.append(data)
        if self.collect:
            self.parts.append(data)


def main():
    fetched = datetime.now(UTC).isoformat()
    rows = []
    try:
        page = get("https://careers.cmcts.com.vn/jobs")
        links = list(dict.fromkeys(re.findall(r"/job/[a-z0-9.-]+-\d+", page)))
        for path in links:
            if not ROLE.search(path):
                continue
            try:
                html = get("https://careers.cmcts.com.vn" + path)
                detail = Detail()
                detail.feed(html)
                title = "".join(detail.title_parts).strip()
                description = "".join(detail.parts).strip()
                deadline = re.search(r'"validThrough"\s*:\s*"(\d{4}-\d{2}-\d{2})"', html)
                if (
                    not title
                    or not description
                    or not deadline
                    or date.fromisoformat(deadline.group(1)) < date.today()
                ):
                    continue
                location = (
                    "Hồ Chí Minh, Việt Nam"
                    if "[HCM]" in title.upper()
                    else "Hà Nội, Việt Nam"
                    if "[HN]" in title.upper()
                    else None
                )
                rows.append(
                    {
                        "source": "employer_ats",
                        "source_job_id": "cmcts:" + path.rsplit("-", 1)[-1],
                        "source_url": "https://careers.cmcts.com.vn" + path,
                        "source_terms_url": "https://careers.cmcts.com.vn/",
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
                )
            except (OSError, ValueError) as e:
                print("CMC:", path, type(e).__name__, flush=True)
            time.sleep(0.5)
    except (OSError, ValueError) as e:
        print("CMC list:", type(e).__name__, flush=True)
    print("CMC:", len(rows), flush=True)
    before = len(rows)
    try:
        for n in range(1, 8):
            html = get(f"https://career.vng.com.vn/vi/tim-kiem-viec-lam?page={n}")
            match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html)
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
            if n >= int(data.get("pages", n)):
                break
            time.sleep(0.5)
    except (OSError, ValueError, KeyError) as e:
        print("VNG:", type(e).__name__, flush=True)
    print("VNG:", len(rows) - before, flush=True)
    path = Path("data/raw") / f"vietnam-companies-{datetime.now(UTC).date()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as out:
        json.dump(rows, out, ensure_ascii=False)
    print("Snapshot:", path, "total:", len(rows), flush=True)


if __name__ == "__main__":
    main()
