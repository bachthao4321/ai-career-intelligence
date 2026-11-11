"""Read-only job presentation; country comes from provenance, never from an LLM."""

from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import Job
from app.ingestion.contracts import json_hash
from app.ingestion.reviewed_titles import REVIEWED_TITLES

TRANSLATION_VERSION = "title-en-vi-v1"
COUNTRIES = {
    "SE": ("Thụy Điển", "Sweden", "Sverige"),
    "VN": ("Việt Nam", "Vietnam", "Viet Nam"),
    "DK": ("Đan Mạch", "Denmark", "Danmark"),
    "NO": ("Na Uy", "Norway", "Norge"),
    "FI": ("Phần Lan", "Finland"),
    "US": ("Hoa Kỳ", "United States", "USA"),
    "GB": ("Anh", "United Kingdom", "UK"),
    "DE": ("Đức", "Germany", "Deutschland", "Tyskland"),
    "FR": ("Pháp", "France", "Frankrike"),
    "CA": ("Canada",),
    "AU": ("Australia", "Úc"),
    "SG": ("Singapore",),
}
ALIASES = {alias.casefold(): code for code, names in COUNTRIES.items() for alias in (code, *names)}


class JobPresentation(BaseModel):
    original_title: str
    country_codes: list[str]
    country_names: list[str]
    country_label: str
    title_en: str | None = None
    title_vi: str | None = None
    translation_model: str | None = None


def translation_signature(settings: Settings) -> str:
    return json_hash(
        [TRANSLATION_VERSION, settings.agent_model_base_url, settings.agent_model_name]
    )


def title_key(title: str) -> str:
    return json_hash([TRANSLATION_VERSION, title])


def country_metadata(source: str, raw: dict[str, Any]) -> tuple[list[str], str]:
    candidates: list[Any] = []
    if source == "jobtech":
        address = raw.get("workplace_address")
        if isinstance(address, dict):
            candidates.append(address.get("country"))
    elif source in {"itviec", "topcv"}:
        posting = raw.get("posting")
        places = posting.get("jobLocation", []) if isinstance(posting, dict) else []
        if isinstance(places, dict):
            places = [places]
        if isinstance(places, list):
            for place in places[:20]:
                address = place.get("address") if isinstance(place, dict) else None
                if isinstance(address, dict):
                    country = address.get("addressCountry")
                    if isinstance(country, dict):
                        country = country.get("name")
                    candidates.append(country)
    elif source == "remotive":
        location = raw.get("candidate_required_location")
        if isinstance(location, str):
            if location.strip().casefold() in {"worldwide", "anywhere", "global"}:
                return [], "Toàn cầu"
            # A regional/timezone eligibility string does not establish one job country.
            if "," in location or location.strip().casefold() == "europe":
                return [], "Nhiều khu vực" if "," in location else "Châu Âu"
            candidates.append(location)
    codes = sorted(
        {
            ALIASES[c.strip().casefold()]
            for c in candidates
            if isinstance(c, str) and c.strip().casefold() in ALIASES
        }
    )
    return codes, " · ".join(COUNTRIES[code][0] for code in codes) or "Chưa rõ quốc gia"


async def presentations(
    session: AsyncSession, jobs: list[Job], settings: Settings
) -> dict[str, JobPresentation]:
    # Machine output is stored separately but is not shown until reviewed.
    # Exact source matching prevents reviews leaking to a changed job title.
    result = {}
    for job in jobs:
        codes, label = country_metadata(job.source, job.raw_payload)
        translated = REVIEWED_TITLES.get(job.title)
        result[str(job.id)] = JobPresentation(
            original_title=job.title,
            country_codes=codes,
            country_names=[COUNTRIES[code][0] for code in codes],
            country_label=label,
            title_en=translated[0] if translated else None,
            title_vi=translated[1] if translated else None,
            translation_model="human-reviewed" if translated else None,
        )
    return result
