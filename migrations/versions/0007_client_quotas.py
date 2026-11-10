"""Persistent hashed-client request limits."""

import sqlalchemy as sa
from alembic import op

revision = "0007_client_quotas"
down_revision = "0006_llm_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_quotas",
        sa.Column("client_hash", sa.String(64), primary_key=True),
        sa.Column("category", sa.String(16), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_client_quotas_expires_at", "client_quotas", ["expires_at"])


def downgrade() -> None:
    op.drop_table("client_quotas")
