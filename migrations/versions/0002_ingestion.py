"""Add provenance snapshots, skills, extraction audit, alias review and call budget."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_ingestion"
down_revision = "0001_create_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.add_column("jobs", sa.Column("source_terms_url", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("raw_hash", sa.String(64), nullable=True))
    op.add_column(
        "jobs", sa.Column("workplace_type", sa.String(20), server_default="unknown", nullable=False)
    )
    op.add_column("jobs", sa.Column("language", sa.String(2), nullable=True))
    op.add_column(
        "jobs",
        sa.Column(
            "structured_skills",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_table(
        "skills",
        sa.Column("slug", sa.String(80), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
    )
    op.create_table(
        "skill_aliases",
        sa.Column("normalized_alias", sa.String(100), primary_key=True),
        sa.Column("surface", sa.String(100), nullable=False),
        sa.Column("skill_slug", sa.String(80), sa.ForeignKey("skills.slug"), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("reviewed_by", sa.String(100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "job_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_terms_url", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "raw_hash", name="uq_job_snapshots_job_raw_hash"),
    )
    op.create_index("ix_job_snapshots_job_id", "job_snapshots", ["job_id"])
    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("extractor_version", sa.String(150), nullable=False),
        sa.Column("catalog_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column(
            "usage", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "job_id",
            "content_hash",
            "extractor_version",
            "catalog_version",
            name="uq_extraction_runs_cache",
        ),
    )
    op.create_index("ix_extraction_runs_job_id", "extraction_runs", ["job_id"])
    op.create_table(
        "job_skills",
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("skill_slug", sa.String(80), sa.ForeignKey("skills.slug"), primary_key=True),
        sa.Column(
            "extraction_run_id",
            sa.Uuid(),
            sa.ForeignKey("extraction_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_field", sa.String(30), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.CheckConstraint(
            "start_offset >= 0 AND end_offset > start_offset", name="ck_job_skills_offsets"
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_job_skills_confidence"),
    )
    op.create_table(
        "alias_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("normalized_alias", sa.String(100), nullable=False),
        sa.Column("surface", sa.String(100), nullable=False),
        sa.Column("skill_slug", sa.String(80), sa.ForeignKey("skills.slug"), nullable=False),
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("mention", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("reviewed_by", sa.String(100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "normalized_alias", "skill_slug", name="uq_alias_candidates_alias_skill"
        ),
    )
    op.create_table(
        "llm_daily_usage",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("calls", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("calls >= 0", name="ck_llm_daily_usage_calls"),
    )


def downgrade() -> None:
    for table in (
        "llm_daily_usage",
        "alias_candidates",
        "job_skills",
        "extraction_runs",
        "job_snapshots",
        "skill_aliases",
        "skills",
    ):
        op.drop_table(table)
    for column in (
        "structured_skills",
        "language",
        "workplace_type",
        "raw_hash",
        "source_terms_url",
        "updated_at",
    ):
        op.drop_column("jobs", column)
