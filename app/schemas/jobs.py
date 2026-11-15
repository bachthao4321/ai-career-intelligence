import uuid
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.policies import local_research_only
from app.schemas.search import SearchFilters

Language = Literal["en", "vi", "other", "unknown"]
Target = Literal["en", "vi"]


class JobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    company: str | None
    location: str | None
    workplace_type: str
    source: str
    source_job_id: str
    source_url: str
    source_terms_url: str | None
    published_at: datetime | None
    fetched_at: datetime
    usage_scope: Literal["local_research_only", "source_terms_apply"] = "source_terms_apply"

    @model_validator(mode="after")
    def set_usage_scope(self) -> Self:
        self.usage_scope = (
            "local_research_only"
            if local_research_only(self.source, self.source_url)
            else "source_terms_apply"
        )
        return self


class JobPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[JobSummary]


class SkillEvidence(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    skill_slug: str
    source_field: str
    evidence: str
    start_offset: int
    end_offset: int
    confidence: float
    method: str
    requirement: str = "unspecified"
    requirement_cue: str | None = None


class JobFactsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    version: str
    seniority: str = "unknown"
    seniority_evidence: str | None = None
    experience_years_min: int | None = None
    experience_evidence: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    salary_evidence: str | None = None
    application_deadline: date | None = None


class JobDetail(JobSummary):
    description: str
    description_language: Language
    structured_skills: list[str]
    content_hash: str
    skills: list[SkillEvidence]
    facts: JobFactsOut | None = None
    note: str = (
        "Skills are literal mentions, not proof of required proficiency. Requirement levels "
        "come from nearby wording or section headings; 'unspecified' means no explicit cue. "
        "Facts are copied from explicit phrases (see *_evidence); empty means not stated. "
        "Attribution: see source and source_url."
    )


class DescriptionRequest(BaseModel):
    target: Target


class DescriptionResponse(BaseModel):
    title: str
    title_source: Literal["reviewed", "machine"]
    text: str
    target: Target


class BrowseParams(SearchFilters):
    limit: int = Field(default=8, ge=1, le=20)
    offset: int = Field(default=0, ge=0, le=10000)


class BrowseJob(JobSummary):
    excerpt: str
    skills: list[str]
    duplicate_listings: int


class BrowsePage(BaseModel):
    total_listings: int
    total: int
    limit: int
    offset: int
    items: list[BrowseJob]


class JobPresentation(BaseModel):
    original_title: str
    country_codes: list[str]
    country_names: list[str]
    country_label: str
    title_en: str | None = None
    title_vi: str | None = None
    translation_model: str | None = None
    application_deadline: date | None = None
    source_excerpt: str = ""
