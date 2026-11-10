"""Operator bootstrap for the fixed application login, separate from migration owner."""

import json
import os

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.db.models import Base


def provision_application(admin_url: str, password: str) -> None:
    if len(password) < 32:
        raise ValueError("Application database password must contain at least 32 characters")
    url = make_url(admin_url).set(drivername="postgresql")
    with psycopg.connect(url.render_as_string(hide_password=False)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR "
                "rolbypassrls OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=pg_roles.oid) "
                "FROM pg_roles WHERE rolname='career_app'"
            )
            existing = cursor.fetchone()
            if existing and existing[0]:
                raise ValueError("Refusing to modify an elevated application role")
            action = "ALTER" if existing else "CREATE"
            cursor.execute(
                sql.SQL(
                    action + " ROLE career_app LOGIN NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Literal(password))
            )
            cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            cursor.execute("GRANT USAGE ON SCHEMA public TO career_app")
            for name in sorted(Base.metadata.tables):
                cursor.execute(
                    sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON public.{} TO career_app").format(
                        sql.Identifier(name)
                    )
                )
            cursor.execute("ALTER ROLE career_app SET statement_timeout TO '5000ms'")
            cursor.execute("ALTER ROLE career_app SET lock_timeout TO '1000ms'")


def main() -> None:
    try:
        provision_application(
            Settings().database_url.get_secret_value(), os.environ["APP_ROLE_PASSWORD"]
        )
    except Exception as exc:
        print(
            json.dumps(
                {"error": type(exc).__name__, "message": "Application role provisioning failed"}
            )
        )
        raise SystemExit(1) from None
    print(json.dumps({"role": "career_app", "provisioned": True}))


if __name__ == "__main__":
    main()
