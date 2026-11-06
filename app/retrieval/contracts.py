import uuid
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.contracts import json_hash
from app.ingestion.policies import local_research_only

Slug = Annotated[str, Field(pattern=r"^[a-z0-9_-]+$", min_length=1, max_length=100)]


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source: Slug | None = None
    location: str | None = Field(default=None, min_length=1, max_length=120)
    workplace_type: Literal["remote", "hybrid", "onsite", "unknown"] | None = None
    skills: list[Slug] = Field(default_factory=list, max_length=16)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=2, max_length=500)
    mode: Literal["bm25", "dense", "hybrid"] = "bm25"
    top_k: int = Field(default=5, ge=1, le=20)
    candidate_k: int = Field(default=50, ge=20, le=100)
    filters: SearchFilters = Field(default_factory=SearchFilters)


@dataclass(frozen=True)
class SearchDocument:
    job_id: uuid.UUID
    content_hash: str
    source: str
    source_job_id: str
    source_url: str
    title: str
    company: str | None
    location: str | None
    workplace_type: str
    text: str
    skills: tuple[str, ...]
    truncated: bool = False

    @property
    def private(self) -> bool:
        return local_research_only(self.source, self.source_url)

    @property
    def index_hash(self) -> str:
        return json_hash(
            {
                "text": self.text,
                "source": self.source,
                "source_url": self.source_url,
                "location": self.location,
                "workplace_type": self.workplace_type,
                "scope": self.private,
                "content_hash": self.content_hash,
                "format": "search-document-v1",
            }
        )


@dataclass(frozen=True)
class RankedHit:
    job_id: uuid.UUID
    score: float
    chunk_index: int | None = None


class SearchHit(BaseModel):
    job_id: uuid.UUID
    title: str
    company: str | None
    location: str | None
    workplace_type: str
    source: str
    source_job_id: str
    source_url: str
    usage_scope: Literal["local_research_only", "source_terms_apply"]
    content_hash: str
    duplicate_listings: int
    score: float
    bm25_score: float | None
    dense_score: float | None
    excerpt: str


class SearchResponse(BaseModel):
    requested_mode: str
    effective_mode: str
    items: list[SearchHit]
    applied_filters: SearchFilters
    eligible_listings: int
    unique_documents: int
    indexed_documents: int | None
    warnings: list[str]
    latency_ms: float
    model_signature: str | None = None
    rrf_k: int = 60
    rrf_weights: tuple[float, float] = (0.5, 1.0)
