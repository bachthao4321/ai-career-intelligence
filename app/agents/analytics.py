import json
import re
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings
from app.db.models import Base
from app.security.sql import ValidatedSQL, validate_sql


class AnalyticsUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class AnalyticsResult:
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_limit_reached: bool
    values_truncated: bool


def view_names(scope: str) -> list[str]:
    return [f"analytics_{scope}_jobs", f"analytics_{scope}_job_skills"]


def provision_role(admin_url: str, schema: str, role: str, password: str, scope: str) -> None:
    """Explicit operator step, never invoked by the HTTP API or migration."""
    if (
        scope not in {"local", "public"}
        or len(password) < 16
        or not all(re.fullmatch(r"[a-z][a-z0-9_]{0,62}", v) for v in (schema, role))
    ):
        raise ValueError("Invalid role provisioning parameters")
    url = make_url(admin_url).set(drivername="postgresql")
    with psycopg.connect(url.render_as_string(hide_password=False)) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls "
                "FROM pg_roles WHERE rolname = %s",
                (role,),
            )
            existing = cursor.fetchone()
            if existing and existing[0]:
                raise ValueError("Refusing to modify an elevated role")
            cursor.execute(
                "SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member "
                "WHERE r.rolname=%s",
                (role,),
            )
            if cursor.fetchone():
                raise ValueError("Refusing to modify a role with memberships")
            action = "ALTER" if existing else "CREATE"
            cursor.execute(
                sql.SQL(
                    action + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(role), sql.Literal(password))
            )
            for setting, value in (
                ("default_transaction_read_only", "on"),
                ("statement_timeout", "1500ms"),
                ("lock_timeout", "500ms"),
                ("search_path", "pg_catalog"),
            ):
                cursor.execute(
                    sql.SQL("ALTER ROLE {} SET {} TO {}").format(
                        sql.Identifier(role), sql.Identifier(setting), sql.Literal(value)
                    )
                )
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            for other_scope in ("local", "public"):
                for view in view_names(other_scope):
                    cursor.execute(
                        sql.SQL("REVOKE ALL ON {}.{} FROM {}").format(
                            sql.Identifier(schema), sql.Identifier(view), sql.Identifier(role)
                        )
                    )
            for view in view_names(scope):
                cursor.execute(
                    sql.SQL("GRANT SELECT ON {}.{} TO {}").format(
                        sql.Identifier(schema), sql.Identifier(view), sql.Identifier(role)
                    )
                )


class AnalyticsExecutor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.scope = "local" if settings.app_env == "local" else "public"
        self.engine: AsyncEngine | None = None
        if settings.analytics_database_url:
            url = make_url(settings.analytics_database_url.get_secret_value())
            if url.drivername != "postgresql+psycopg" or not url.username:
                raise ValueError("Analytics requires a separate postgresql+psycopg login")
            self.engine = create_async_engine(
                url,
                pool_size=2,
                max_overflow=0,
                pool_timeout=2,
                connect_args={
                    "connect_timeout": 3,
                    "options": f"-c statement_timeout={settings.analytics_timeout_ms} "
                    "-c lock_timeout=500 "
                    "-c default_transaction_read_only=on -c search_path=pg_catalog",
                },
            )

    async def close(self) -> None:
        if self.engine:
            await self.engine.dispose()

    async def execute(self, generated_sql: str) -> AnalyticsResult:
        # Validate inside the executor: model output and callers are both untrusted.
        query = validate_sql(generated_sql, schema=self.settings.analytics_schema, scope=self.scope)
        return await self._execute_validated(query)

    async def _execute_validated(self, query: ValidatedSQL) -> AnalyticsResult:
        if self.engine is None:
            raise AnalyticsUnavailable("analytics_not_configured")
        try:
            async with self.engine.connect() as conn:
                async with conn.begin():
                    await conn.exec_driver_sql(
                        "SET TRANSACTION READ ONLY, ISOLATION LEVEL REPEATABLE READ"
                    )
                    await conn.execute(
                        text("SELECT set_config('statement_timeout', :ms, true)"),
                        {"ms": str(self.settings.analytics_timeout_ms)},
                    )
                    role = (
                        (
                            await conn.execute(
                                text(
                                    "SELECT rolsuper, rolcreaterole, rolcreatedb, "
                                    "rolreplication, rolbypassrls, "
                                    "EXISTS (SELECT 1 FROM pg_auth_members "
                                    "WHERE member=pg_roles.oid) AS member "
                                    "FROM pg_roles WHERE rolname=current_user"
                                )
                            )
                        )
                        .mappings()
                        .one()
                    )
                    if any(role.values()):
                        raise AnalyticsUnavailable("analytics_role_is_not_restricted")
                    schema = self.settings.analytics_schema
                    # Reject accidental reuse of app/admin credentials or pre-existing grants.
                    for table in (*Base.metadata.tables, "alembic_version"):
                        privileged = await conn.scalar(
                            text(
                                "SELECT has_table_privilege(current_user, :relation, "
                                "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                                "OR has_any_column_privilege(current_user, :relation, "
                                "'SELECT,INSERT,UPDATE,REFERENCES')"
                            ),
                            {"relation": f'"{schema}"."{table}"'},
                        )
                        if privileged:
                            raise AnalyticsUnavailable("analytics_role_has_base_table_access")
                    if await conn.scalar(
                        text("SELECT has_schema_privilege(current_user, :s, 'CREATE')"),
                        {"s": schema},
                    ):
                        raise AnalyticsUnavailable("analytics_role_can_create_objects")
                    if self.scope == "public":
                        for view in view_names("local"):
                            if await conn.scalar(
                                text(
                                    "SELECT has_table_privilege(current_user, :r, 'SELECT') "
                                    "OR has_any_column_privilege(current_user, :r, 'SELECT')"
                                ),
                                {"r": f'"{schema}"."{view}"'},
                            ):
                                raise AnalyticsUnavailable("public_role_has_local_access")
                    # SQL has already been parsed/rebuilt by the allowlist validator.
                    # Literal LIKE wildcards must not be read as psycopg placeholders.
                    result = await conn.exec_driver_sql(
                        query.sql, execution_options={"no_parameters": True}
                    )
                    columns = list(result.keys())
                    if len(set(columns)) != len(columns):
                        raise AnalyticsUnavailable("duplicate_result_columns")
                    raw = result.fetchmany(query.limit)
                    rows: list[dict[str, Any]] = []
                    truncated = False
                    for row in raw:
                        values: dict[str, Any] = {}
                        for key, value in zip(columns, row, strict=True):
                            if not isinstance(value, (str, int, float, bool, type(None))):
                                value = str(value)
                            if isinstance(value, str) and len(value) > 300:
                                value, truncated = value[:300], True
                            values[key] = value
                        rows.append(values)
                    if len(json.dumps(rows, ensure_ascii=False).encode()) > 65536:
                        raise AnalyticsUnavailable("result_too_large")
                    return AnalyticsResult(
                        query.sql, columns, rows, len(raw) == query.limit, truncated
                    )
        except SQLAlchemyError as exc:
            raise AnalyticsUnavailable("analytics_query_failed_or_timed_out") from exc
