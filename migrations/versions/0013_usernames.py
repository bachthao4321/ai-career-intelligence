"""Usernames: unique handle shown in the UI and accepted at login."""

import sqlalchemy as sa
from alembic import op

revision = "0013_usernames"
down_revision = "0012_application_status"
branch_labels = None
depends_on = None

USERNAME_RULE = "username ~ '^[a-z0-9][a-z0-9._]{1,28}[a-z0-9]$'"


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(30), nullable=True))
    # Backfill from the email local part; later duplicates get a stable id-derived suffix.
    op.execute(
        """
        UPDATE users AS u SET username = named.name FROM (
          SELECT id,
                 CASE WHEN rn = 1 THEN stem
                      ELSE left(stem, 20) || '_' || left(md5(id::text), 6) END AS name
          FROM (
            SELECT id, stem,
                   row_number() OVER (PARTITION BY stem ORDER BY created_at, id) AS rn
            FROM (
              SELECT id, created_at,
                     CASE WHEN length(cleaned) >= 3 THEN cleaned ELSE 'user' END AS stem
              FROM (
                SELECT id, created_at,
                       trim(both '._' from left(trim(both '._' from regexp_replace(
                         lower(split_part(email, '@', 1)), '[^a-z0-9._]', '', 'g')), 26)) AS cleaned
                FROM users
              ) AS raw
            ) AS stems
          ) AS ranked
        ) AS named
        WHERE u.id = named.id
        """
    )
    op.alter_column("users", "username", nullable=False)
    op.create_unique_constraint("uq_users_username", "users", ["username"])
    op.create_check_constraint("ck_users_username", "users", USERNAME_RULE)


def downgrade() -> None:
    op.drop_constraint("ck_users_username", "users", type_="check")
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_column("users", "username")
