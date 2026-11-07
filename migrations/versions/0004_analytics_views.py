"""Scoped, metadata-only analytics views; role provisioning is an operator action."""

from alembic import op

revision = "0004_analytics_views"
down_revision = "0003_vector_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All relations resolve in the migration's schema, including isolated CI schemas.
    conn = op.get_bind()
    schema = conn.exec_driver_sql("SELECT current_schema()").scalar_one()
    quote = conn.dialect.identifier_preparer.quote_identifier
    prefix = quote(schema) + "."
    protected = r"^https?://([^/?#]*@)?([^/?#]*\.)?(itviec\.com|topcv\.vn)(:\d+)?(/|$)"
    for scope in ("local", "public"):
        predicate = (
            "TRUE"
            if scope == "local"
            else ("j.source NOT IN ('itviec', 'topcv') AND j.source_url !~* '" + protected + "'")
        )
        conn.exec_driver_sql(
            f"CREATE VIEW {prefix}analytics_{scope}_jobs WITH (security_barrier=true) AS "
            "SELECT j.id, j.source, j.title, j.company, j.location, j.workplace_type, "
            "j.content_hash, j.published_at, j.fetched_at "
            f"FROM {prefix}jobs j WHERE {predicate}"
        )
        conn.exec_driver_sql(
            f"CREATE VIEW {prefix}analytics_{scope}_job_skills "
            "WITH (security_barrier=true) AS SELECT s.job_id, s.skill_slug "
            f"FROM {prefix}job_skills s JOIN {prefix}analytics_{scope}_jobs j "
            "ON j.id = s.job_id AND j.content_hash = s.content_hash"
        )


def downgrade() -> None:
    for scope in ("local", "public"):
        op.execute(f"DROP VIEW analytics_{scope}_job_skills")
        op.execute(f"DROP VIEW analytics_{scope}_jobs")
