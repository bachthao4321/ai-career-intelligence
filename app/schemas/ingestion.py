import hashlib
import json
import unicodedata
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

EvidenceField = Literal["title", "description", "structured_skills"]
EVIDENCE_FIELDS: tuple[EvidenceField, ...] = ("title", "description", "structured_skills")


def normalize_text(value: str) -> str:
    return "\n".join(
        " ".join(line.split())
        for line in unicodedata.normalize("NFC", value).replace("\r\n", "\n").splitlines()
        if line.strip()
    ).strip()


def alias_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class JobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_-]+$")
    source_job_id: str = Field(min_length=1, max_length=255)
    source_url: HttpUrl
    source_terms_url: HttpUrl
    title: str = Field(min_length=1, max_length=1000)
    company: str | None = Field(default=None, max_length=1000)
    location: str | None = Field(default=None, max_length=2000)
    workplace_type: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")
    description: str = Field(min_length=1, max_length=200_000)
    structured_skills: list[str] = Field(default_factory=list, max_length=128)
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "description", mode="before")
    @classmethod
    def normalize_content(cls, value: Any) -> Any:
        return normalize_text(value) if isinstance(value, str) else value

    @field_validator("structured_skills")
    @classmethod
    def validate_tags(cls, values: list[str]) -> list[str]:
        cleaned = [normalize_text(value) for value in values]
        if any(not value or len(value) > 100 for value in cleaned):
            raise ValueError("Structured skills must contain 1–100 characters each")
        return sorted(set(cleaned), key=alias_key)

    @field_validator("published_at", "fetched_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Timestamps must include a timezone")
        return value.astimezone(UTC) if value is not None else None

    def evidence_text(self, field: EvidenceField) -> str:
        if field == "structured_skills":
            return "\n".join(self.structured_skills)
        return self.title if field == "title" else self.description

    @property
    def input_characters(self) -> int:
        return sum(len(self.evidence_text(field)) for field in EVIDENCE_FIELDS)

    @property
    def content_hash(self) -> str:
        # Source identity, fetch times and HTML-only changes do not change this fingerprint.
        return json_hash(
            {
                key: getattr(self, key)
                for key in (
                    "title",
                    "company",
                    "location",
                    "workplace_type",
                    "language",
                    "description",
                    "structured_skills",
                )
            }
        )


class SkillMention(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    skill: str = Field(min_length=1, max_length=80)
    source_field: EvidenceField
    evidence: str = Field(min_length=1, max_length=100)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    confidence: float = Field(ge=0, le=1)


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    skills: list[SkillMention] = Field(max_length=256)
