"""Requirement level (required/preferred/negated) for grounded job skill mentions."""

import sqlalchemy as sa
from alembic import op

revision = "0009_skill_requirements"
down_revision = "0008_job_title_translations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_skills",
        sa.Column("requirement", sa.String(12), server_default="unspecified", nullable=False),
    )
    op.add_column("job_skills", sa.Column("requirement_cue", sa.String(80), nullable=True))
    op.create_check_constraint(
        "ck_job_skills_requirement",
        "job_skills",
        "requirement IN ('required', 'preferred', 'negated', 'unspecified')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_job_skills_requirement", "job_skills", type_="check")
    op.drop_column("job_skills", "requirement_cue")
    op.drop_column("job_skills", "requirement")
