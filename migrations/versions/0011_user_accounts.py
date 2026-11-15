"""User accounts, server-side sessions and saved jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0011_user_accounts"
down_revision = "0010_job_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("email = lower(email) AND length(email) <= 254", name="ck_users_email"),
    )
    op.create_table(
        "user_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])
    op.create_table(
        "saved_jobs",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_saved_jobs_job_id", "saved_jobs", ["job_id"])


def downgrade() -> None:
    op.drop_table("saved_jobs")
    op.drop_table("user_sessions")
    op.drop_table("users")
