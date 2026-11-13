"""Evidence-backed job facts and their scoped analytics views."""

import sqlalchemy as sa
from alembic import op

revision = "0010_job_facts"
down_revision = "0009_skill_requirements"
branch_labels = None
depends_on = None


def _prefix() -> str:
    conn = op.get_bind()
    schema = conn.exec_driver_sql("SELECT current_schema()").scalar_one()
    return conn.dialect.identifier_preparer.quote_identifier(schema) + "."


def upgrade() -> None:
    op.create_table(
        "job_facts",
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("seniority", sa.String(10), server_default="unknown", nullable=False),
        sa.Column("seniority_evidence", sa.String(80), nullable=True),
        sa.Column("experience_years_min", sa.Integer(), nullable=True),
        sa.Column("experience_evidence", sa.String(160), nullable=True),
        sa.Column("salary_min", sa.BigInteger(), nullable=True),
        sa.Column("salary_max", sa.BigInteger(), nullable=True),
        sa.Column("salary_currency", sa.String(3), nullable=True),
        sa.Column("salary_period", sa.String(5), nullable=True),
        sa.Column("salary_evidence", sa.String(200), nullable=True),
        sa.Column("application_deadline", sa.Date(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "seniority IN ('intern', 'junior', 'mid', 'senior', 'lead', 'unknown')",
            name="ck_job_facts_seniority",
        ),
        sa.CheckConstraint(
            "experience_years_min IS NULL OR experience_years_min BETWEEN 0 AND 30",
            name="ck_job_facts_experience",
        ),
        sa.CheckConstraint(
            "salary_min IS NULL OR (salary_min > 0 AND salary_max >= salary_min "
            "AND salary_currency IS NOT NULL)",
            name="ck_job_facts_salary",
        ),
    )
    op.create_index("ix_job_facts_deadline", "job_facts", ["application_deadline"])
    prefix = _prefix()
    conn = op.get_bind()
    for scope in ("local", "public"):
        # CREATE OR REPLACE keeps existing grants; new columns are appended at the end.
        conn.exec_driver_sql(
            f"CREATE OR REPLACE VIEW {prefix}analytics_{scope}_job_skills "
            "WITH (security_barrier=true) AS SELECT s.job_id, s.skill_slug, s.requirement "
            f"FROM {prefix}job_skills s JOIN {prefix}analytics_{scope}_jobs j "
            "ON j.id = s.job_id AND j.content_hash = s.content_hash"
        )
        conn.exec_driver_sql(
            f"CREATE VIEW {prefix}analytics_{scope}_job_facts WITH (security_barrier=true) AS "
            "SELECT f.job_id, f.seniority, f.experience_years_min, f.salary_min, f.salary_max, "
            "f.salary_currency, f.salary_period, f.application_deadline "
            f"FROM {prefix}job_facts f JOIN {prefix}analytics_{scope}_jobs j "
            "ON j.id = f.job_id AND j.content_hash = f.content_hash"
        )


def downgrade() -> None:
    prefix = _prefix()
    conn = op.get_bind()
    for scope in ("local", "public"):
        conn.exec_driver_sql(f"DROP VIEW {prefix}analytics_{scope}_job_facts")
        conn.exec_driver_sql(f"DROP VIEW {prefix}analytics_{scope}_job_skills")
        conn.exec_driver_sql(
            f"CREATE VIEW {prefix}analytics_{scope}_job_skills "
            "WITH (security_barrier=true) AS SELECT s.job_id, s.skill_slug "
            f"FROM {prefix}job_skills s JOIN {prefix}analytics_{scope}_jobs j "
            "ON j.id = s.job_id AND j.content_hash = s.content_hash"
        )
    op.drop_index("ix_job_facts_deadline", table_name="job_facts")
    op.drop_table("job_facts")
