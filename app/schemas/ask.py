from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.search import SearchResponse


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=2, max_length=500)
    search_mode: Literal["bm25", "dense", "hybrid"] = "hybrid"
    top_k: int = Field(default=5, ge=1, le=20)


class AskResponse(BaseModel):
    route: Literal["search", "analytics", "clarify"]
    status: Literal["ok", "needs_clarification", "rejected", "unavailable"]
    answer: str
    planner: Literal["deterministic", "model"]
    scope: Literal["local_research", "public_sources"]
    warnings: list[str] = Field(default_factory=list)
    search: SearchResponse | None = None
    analytics: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
