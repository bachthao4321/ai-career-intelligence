"""Application status on saved jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0012_application_status"
down_revision = "0011_user_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "saved_jobs",
        sa.Column("status", sa.String(16), server_default="saved", nullable=False),
    )
    op.add_column(
        "saved_jobs",
        sa.Column(
            "status_updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_saved_jobs_status",
        "saved_jobs",
        "status IN ('saved', 'applied', 'interviewing', 'offer', 'rejected')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_saved_jobs_status", "saved_jobs", type_="check")
    op.drop_column("saved_jobs", "status_updated_at")
    op.drop_column("saved_jobs", "status")
