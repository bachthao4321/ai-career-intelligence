"""Create the initial jobs table with source identity and provenance."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_create_jobs"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("source_job_id", sa.String(255), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("company", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "raw_payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "source_job_id", name="uq_jobs_source_source_job_id"),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_jobs_title_not_blank"),
        sa.CheckConstraint("length(trim(description)) > 0", name="ck_jobs_description_not_blank"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_jobs_content_hash_sha256"),
    )
    op.create_index("ix_jobs_content_hash", "jobs", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_jobs_content_hash", table_name="jobs")
    op.drop_table("jobs")
