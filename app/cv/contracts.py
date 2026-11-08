import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.retrieval.contracts import SearchFilters, SourceSkillEvidence

MAX_PDF_BYTES = 2 * 1024 * 1024
MAX_PAGES = 10
MAX_TEXT_CHARACTERS = 40000
MAX_CONTENT_BYTES = 4 * 1024 * 1024
PARSER_TIMEOUT_SECONDS = 6


class CVError(ValueError):
    """Safe fixed codes only; never include input, paths or parser diagnostics."""


class CVPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: int = Field(ge=1, le=MAX_PAGES)
    text: str = Field(max_length=MAX_TEXT_CHARACTERS)


class CVEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(ge=1, le=MAX_PAGES)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=100)


class CVSkill(BaseModel):
    slug: str
    name: str
    evidence: list[CVEvidence] = Field(min_length=1, max_length=5)


class CVProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pages: list[CVPage] = Field(min_length=1, max_length=MAX_PAGES)
    skills: list[CVSkill] = Field(max_length=128)
    warnings: list[str]
    extraction_version: str
    catalog_version: str


class CVView(BaseModel):
    id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    profile: CVProfile


class CVUploaded(CVView):
    access_token: str


class MatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    target_role: str | None = Field(default=None, min_length=2, max_length=200)
    top_k: int = Field(default=5, ge=1, le=20)
    filters: SearchFilters = Field(default_factory=SearchFilters)


class MatchedSkill(BaseModel):
    slug: str
    cv_evidence: list[CVEvidence]
    job_evidence: SourceSkillEvidence


class NotSeenSkill(BaseModel):
    slug: str
    job_evidence: SourceSkillEvidence


class CVJobMatch(BaseModel):
    job_id: uuid.UUID
    content_hash: str
    title: str
    company: str | None
    location: str | None
    source: str
    source_url: str
    usage_scope: Literal["local_research_only", "source_terms_apply"]
    duplicate_listings: int
    score: float
    bm25_score: float
    skill_coverage: float | None
    matched_skills: list[MatchedSkill]
    skills_not_seen_in_cv: list[NotSeenSkill]
    cv_skills_not_mentioned_in_job: list[str]


class MatchReport(BaseModel):
    cv_id: uuid.UUID
    expires_at: datetime
    algorithm: str = "cv-bm25-coverage-v1"
    eligible_listings: int
    unique_documents: int
    candidate_documents: int
    filters: SearchFilters
    items: list[CVJobMatch]
    warnings: list[str]
