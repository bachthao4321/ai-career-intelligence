import pytest

from app.security.sql import UnsafeSQL, validate_sql


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM jobs",
        "UPDATE jobs SET title='x'",
        "DROP TABLE jobs",
        "SELECT id FROM jobs; SELECT id FROM jobs",
        "SELECT id INTO stolen FROM jobs",
        "WITH x AS (DELETE FROM jobs RETURNING *) SELECT * FROM x",
        "WITH x AS (SELECT id FROM jobs) SELECT id FROM x",
        "SELECT id FROM jobs UNION SELECT id FROM jobs",
        "SELECT (SELECT COUNT(*) FROM jobs) FROM jobs",
        "SELECT id FROM jobs WHERE id IN (SELECT job_id FROM job_skills)",
        "SELECT pg_sleep(100) FROM jobs",
        "SELECT pg_read_file('/etc/passwd') FROM jobs",
        "SELECT set_config('search_path','public',true) FROM jobs",
        "SELECT pg_advisory_lock(1) FROM jobs",
        "SELECT lo_export(1,'/tmp/x') FROM jobs",
        "SELECT dblink('x','x') FROM jobs",
        "SELECT public.count(id) FROM jobs",
        "SELECT * FROM jobs",
        "SELECT jobs.* FROM jobs",
        "SELECT raw_payload FROM jobs",
        "SELECT description FROM jobs",
        "SELECT ctid FROM jobs",
        "SELECT xmin FROM jobs",
        "SELECT id FROM pg_catalog.pg_class",
        "SELECT id FROM public.jobs",
        "SELECT id FROM analytics_public_jobs",
        "SELECT id FROM job_snapshots",
        "SELECT j.raw_payload FROM jobs j",
        "SELECT a.id FROM jobs j",
        "SELECT id FROM jobs AS j(foo)",
        "SELECT id FROM jobs j, jobs k",
        "SELECT j.id FROM jobs j CROSS JOIN job_skills s",
        "SELECT j.id FROM jobs j JOIN job_skills s ON 1=1",
        "SELECT j.id FROM jobs j LEFT JOIN job_skills s ON j.id=s.job_id",
        "SELECT j.id FROM jobs j JOIN job_skills s ON j.id=s.skill_slug",
        "SELECT j.id FROM jobs j JOIN job_skills s USING (id)",
        "SELECT id FROM jobs FOR UPDATE",
        "SELECT id FROM jobs OFFSET 1",
        "SELECT id FROM jobs TABLESAMPLE SYSTEM(10)",
        "SELECT id FROM jobs LIMIT ALL",
        "SELECT id FROM jobs LIMIT -1",
        "SELECT id FROM jobs LIMIT 0",
        "SELECT id FROM jobs LIMIT 1+1",
        "SELECT id FROM jobs LIMIT '5'",
        "SELECT COUNT(*) OVER () FROM jobs",
        "SELECT id::text FROM jobs",
        "SELECT CAST(id AS text) FROM jobs",
        "SELECT id COLLATE public.evil FROM jobs",
        "SELECT current_user FROM jobs",
        "SELECT COUNT(*) FROM jobs -- bypass",
        "SELECT /* hint */ id FROM jobs",
        "SELECT id FROM jobs WHERE title SIMILAR TO '(a+)+$'",
        "SELECT id FROM jobs WHERE title ~ '.*'",
        "SELECT id FROM generate_series(1,999)",
        "SELECT id AS x, source AS x FROM jobs",
        "SELECT FROM jobs",
        "SELECT id FROM jobs LIMIT " + "9" * 100,
    ],
)
def test_reject_untrusted_sql(query: str) -> None:
    with pytest.raises(UnsafeSQL):
        validate_sql(query)


def test_scope_rewrite_limit_and_quoted_literal() -> None:
    query = validate_sql(
        "SELECT title FROM jobs WHERE company = 'O''Reilly' LIMIT 10000",
        schema="safe_schema",
        scope="public",
    )
    assert '"safe_schema"."analytics_public_jobs" AS jobs' in query.sql
    assert "'O''Reilly'" in query.sql
    assert query.limit == 50 and query.sql.endswith("LIMIT 50")
    local = validate_sql("SELECT COUNT(DISTINCT content_hash) AS n FROM jobs", scope="local")
    assert '"analytics_local_jobs"' in local.sql


def test_valid_join_and_grouped_count() -> None:
    result = validate_sql(
        "SELECT j.source, COUNT(DISTINCT j.id) AS n "
        "FROM jobs j JOIN job_skills s ON j.id=s.job_id "
        "WHERE s.skill_slug IN ('python','sql') GROUP BY j.source "
        "HAVING COUNT(*) > 0 ORDER BY n DESC, j.source LIMIT 10"
    )
    assert result.limit == 10
    assert '"analytics_public_job_skills" AS s' in result.sql


def test_length_and_schema_limits() -> None:
    for query in ("", "x" * 6001):
        with pytest.raises(UnsafeSQL):
            validate_sql(query)
    with pytest.raises(UnsafeSQL):
        validate_sql("SELECT id FROM jobs", schema='public";DROP SCHEMA public')
