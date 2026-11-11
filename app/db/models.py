import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source", "source_job_id", name="uq_jobs_source_source_job_id"),
        CheckConstraint("length(trim(title)) > 0", name="ck_jobs_title_not_blank"),
        CheckConstraint("length(trim(description)) > 0", name="ck_jobs_description_not_blank"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_jobs_content_hash_sha256"),
        Index("ix_jobs_content_hash", "content_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    source: Mapped[str] = mapped_column(String(100))
    source_job_id: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source_terms_url: Mapped[str | None] = mapped_column(Text)
    raw_hash: Mapped[str | None] = mapped_column(String(64))
    workplace_type: Mapped[str] = mapped_column(String(20), server_default="unknown")
    language: Mapped[str | None] = mapped_column(String(2))
    structured_skills: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))


class Skill(Base):
    __tablename__ = "skills"
    slug: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))


class SkillAlias(Base):
    __tablename__ = "skill_aliases"
    normalized_alias: Mapped[str] = mapped_column(String(100), primary_key=True)
    surface: Mapped[str] = mapped_column(String(100))
    skill_slug: Mapped[str] = mapped_column(ForeignKey("skills.slug"))
    origin: Mapped[str] = mapped_column(String(20))
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobSnapshot(Base):
    __tablename__ = "job_snapshots"
    __table_args__ = (UniqueConstraint("job_id", "raw_hash", name="uq_job_snapshots_job_raw_hash"),)
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    raw_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_url: Mapped[str] = mapped_column(Text)
    source_terms_url: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "content_hash",
            "extractor_version",
            "catalog_version",
            name="uq_extraction_runs_cache",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(150))
    catalog_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobSkill(Base):
    __tablename__ = "job_skills"
    __table_args__ = (
        CheckConstraint(
            "start_offset >= 0 AND end_offset > start_offset", name="ck_job_skills_offsets"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_job_skills_confidence"),
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    skill_slug: Mapped[str] = mapped_column(ForeignKey("skills.slug"), primary_key=True)
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extraction_runs.id", ondelete="CASCADE")
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    source_field: Mapped[str] = mapped_column(String(30))
    evidence: Mapped[str] = mapped_column(Text)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(20))


class AliasCandidate(Base):
    __tablename__ = "alias_candidates"
    __table_args__ = (
        UniqueConstraint("normalized_alias", "skill_slug", name="uq_alias_candidates_alias_skill"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    normalized_alias: Mapped[str] = mapped_column(String(100))
    surface: Mapped[str] = mapped_column(String(100))
    skill_slug: Mapped[str] = mapped_column(ForeignKey("skills.slug"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    content_hash: Mapped[str] = mapped_column(String(64))
    mention: Mapped[dict[str, Any]] = mapped_column(JSONB)
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LlmDailyUsage(Base):
    __tablename__ = "llm_daily_usage"
    __table_args__ = (CheckConstraint("calls >= 0", name="ck_llm_daily_usage_calls"),)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, server_default="0")


class VectorIndexState(Base):
    __tablename__ = "vector_index_states"
    namespace: Mapped[str] = mapped_column(String(150), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    index_hash: Mapped[str] = mapped_column(String(64))
    version_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    point_ids: Mapped[list[str]] = mapped_column(JSONB)
    truncated: Mapped[bool] = mapped_column(server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IndexingRun(Base):
    __tablename__ = "indexing_runs"
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    namespace: Mapped[str] = mapped_column(String(150), index=True)
    model_signature: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CVDocument(Base):
    __tablename__ = "cv_documents"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class JobTitleTranslation(Base):
    __tablename__ = "job_title_translations"
    title_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_signature: Mapped[str] = mapped_column(String(64), primary_key=True)
    title_en: Mapped[str] = mapped_column(Text)
    title_vi: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LlmDecision(Base):
    __tablename__ = "llm_decisions"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    task: Mapped[str] = mapped_column(String(40))
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LlmAttempt(Base):
    __tablename__ = "llm_attempts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    bucket: Mapped[str] = mapped_column(String(64), index=True)
    task: Mapped[str] = mapped_column(String(40))
    decision_key: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40))
    elapsed_ms: Mapped[int] = mapped_column(Integer, server_default="0")
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class ClientQuota(Base):
    __tablename__ = "client_quotas"
    client_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(16), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    used: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
