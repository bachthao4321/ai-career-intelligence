"""Bounded model decisions and body-free attempt telemetry."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_llm_runtime"
down_revision = "0005_cv_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_decisions",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("task", sa.String(40), nullable=False),
        sa.Column("value", JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_llm_decisions_expires_at", "llm_decisions", ["expires_at"])
    op.create_table(
        "llm_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("bucket", sa.String(64), nullable=False),
        sa.Column("task", sa.String(40), nullable=False),
        sa.Column("decision_key", sa.String(64), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("usage", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.create_index("ix_llm_attempts_bucket", "llm_attempts", ["bucket"])


def downgrade() -> None:
    op.drop_table("llm_attempts")
    op.drop_table("llm_decisions")
