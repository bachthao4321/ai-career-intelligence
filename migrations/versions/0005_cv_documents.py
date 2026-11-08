"""Expiring CV text profiles; original PDF and bearer tokens are never stored."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_cv_documents"
down_revision = "0004_analytics_views"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cv_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("profile", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cv_documents_expires_at", "cv_documents", ["expires_at"])


def downgrade() -> None:
    op.drop_table("cv_documents")
