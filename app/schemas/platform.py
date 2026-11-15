from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.cv import MAX_PAGES, MAX_PDF_BYTES


class SourceCount(BaseModel):
    source: str
    count: int


class SkillCount(BaseModel):
    slug: str
    name: str
    count: int


class Overview(BaseModel):
    listings: int
    unique_documents: int
    sources: list[SourceCount]
    skills: list[SkillCount]
    latest_fetch: datetime | None
    scope: Literal["local_research", "public_sources"]
    model_planner_enabled: bool
    cv_ttl_seconds: int
    cv_max_bytes: int = MAX_PDF_BYTES
    cv_max_pages: int = MAX_PAGES


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    dependencies: dict[str, Literal["ok", "unavailable"]]
