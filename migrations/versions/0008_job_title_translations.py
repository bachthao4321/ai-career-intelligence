"""Cache machine-translated titles separately from original source evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0008_job_title_translations"
down_revision = "0007_client_quotas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_title_translations",
        sa.Column("title_hash", sa.String(64), primary_key=True),
        sa.Column("model_signature", sa.String(64), primary_key=True),
        sa.Column("title_en", sa.Text(), nullable=False),
        sa.Column("title_vi", sa.Text(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("job_title_translations")
