import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

SCHEMA = {
    "jobs": {
        "id",
        "source",
        "title",
        "company",
        "location",
        "workplace_type",
        "content_hash",
        "published_at",
        "fetched_at",
    },
    "job_skills": {"job_id", "skill_slug"},
}
MAX_ROWS = 50


class UnsafeSQL(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedSQL:
    sql: str
    limit: int


def validate_sql(sql: str, *, schema: str = "public", scope: str = "public") -> ValidatedSQL:
    """Accept a small flat SELECT grammar; everything else fails closed.

    Never execute the original string. Tables are mapped to fixed scoped views.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) or scope not in {"local", "public"}:
        raise UnsafeSQL("invalid_scope")
    if not 1 <= len(sql) <= 6000:
        raise UnsafeSQL("sql_length")
    try:
        statements = sqlglot.parse(sql, read="postgres", error_level=sqlglot.ErrorLevel.RAISE)
    except (sqlglot.errors.SqlglotError, RecursionError) as exc:
        raise UnsafeSQL("parse_error") from exc
    if len(statements) != 1 or type(statements[0]) is not exp.Select:
        raise UnsafeSQL("single_select_required")
    tree = statements[0]
    if not 1 <= len(tree.expressions) <= 12:
        raise UnsafeSQL("invalid_projection_count")
    allowed = {
        exp.Select,
        exp.From,
        exp.Table,
        exp.TableAlias,
        exp.Identifier,
        exp.Column,
        exp.Alias,
        exp.Literal,
        exp.Star,
        exp.Count,
        exp.Distinct,
        exp.Where,
        exp.And,
        exp.Or,
        exp.Not,
        exp.Paren,
        exp.EQ,
        exp.NEQ,
        exp.GT,
        exp.GTE,
        exp.LT,
        exp.LTE,
        exp.Is,
        exp.Null,
        exp.Boolean,
        exp.Like,
        exp.ILike,
        exp.In,
        exp.Group,
        exp.Having,
        exp.Order,
        exp.Ordered,
        exp.Limit,
        exp.Join,
    }
    nodes = list(tree.walk())
    if len(nodes) > 250 or any(type(node) not in allowed for node in nodes):
        raise UnsafeSQL("unsupported_sql_node")
    if any(node.comments for node in nodes) or len(list(tree.find_all(exp.Select))) != 1:
        raise UnsafeSQL("comments_or_nested_query")
    if any(
        value
        for key, value in tree.args.items()
        if key
        not in {
            "expressions",
            "from_",
            "joins",
            "where",
            "group",
            "having",
            "order",
            "limit",
            "distinct",
        }
    ):
        raise UnsafeSQL("unsupported_select_clause")
    tables = list(tree.find_all(exp.Table))
    if not 1 <= len(tables) <= 2 or len({t.name for t in tables}) != len(tables):
        raise UnsafeSQL("invalid_tables")
    aliases: dict[str, str] = {}
    for table in tables:
        if table.name not in SCHEMA or table.db or table.catalog:
            raise UnsafeSQL("table_not_allowed")
        alias = table.args.get("alias")
        if alias and alias.args.get("columns"):
            raise UnsafeSQL("table_column_aliases")
        name = table.alias_or_name
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name) or name in aliases:
            raise UnsafeSQL("invalid_table_alias")
        aliases[name] = table.name
    output_aliases = [node.alias for node in tree.expressions if isinstance(node, exp.Alias)]
    if len(set(output_aliases)) != len(output_aliases):
        raise UnsafeSQL("duplicate_output_alias")
    for alias in output_aliases:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", alias):
            raise UnsafeSQL("invalid_output_alias")
    for star in tree.find_all(exp.Star):
        if type(star.parent) is not exp.Count or any(star.args.values()):
            raise UnsafeSQL("star_only_in_count")
    for column in tree.find_all(exp.Column):
        if column.db or column.catalog:
            raise UnsafeSQL("qualified_column")
        if column.table:
            if column.table not in aliases or column.name not in SCHEMA[aliases[column.table]]:
                raise UnsafeSQL("column_not_allowed")
        else:
            matches = [key for key, table in aliases.items() if column.name in SCHEMA[table]]
            if len(matches) == 1:
                column.set("table", exp.to_identifier(matches[0]))
            elif column.name in output_aliases and isinstance(column.parent, exp.Ordered):
                pass
            else:
                raise UnsafeSQL("unknown_or_ambiguous_column")
    joins = tree.args.get("joins") or []
    if len(tables) == 2:
        if len(joins) != 1:
            raise UnsafeSQL("invalid_join")
        join = joins[0]
        on = join.args.get("on")
        if (
            join.args.get("side")
            or join.args.get("method")
            or join.args.get("using")
            or join.args.get("kind") not in {None, "", "INNER"}
            or type(on) is not exp.EQ
        ):
            raise UnsafeSQL("only_inner_key_join")
        keys = set()
        for column in (on.this, on.expression):
            if type(column) is not exp.Column or column.table not in aliases:
                raise UnsafeSQL("only_inner_key_join")
            keys.add((aliases[column.table], column.name))
        if keys != {("jobs", "id"), ("job_skills", "job_id")}:
            raise UnsafeSQL("only_inner_key_join")
    elif joins:
        raise UnsafeSQL("invalid_join")
    limit = MAX_ROWS
    clause = tree.args.get("limit")
    if clause:
        value = clause.expression
        if type(value) is not exp.Literal or value.is_string or not value.this.isdecimal():
            raise UnsafeSQL("literal_limit_required")
        if len(value.this) > 7 or not 1 <= int(value.this) <= 1_000_000:
            raise UnsafeSQL("invalid_limit")
        limit = min(MAX_ROWS, int(value.this))
    tree.set("limit", exp.Limit(expression=exp.Literal.number(limit)))
    for table in tables:
        original = table.name
        if not table.alias:
            table.set("alias", exp.TableAlias(this=exp.to_identifier(original)))
        table.set("this", exp.to_identifier(f"analytics_{scope}_{original}", quoted=True))
        table.set("db", exp.to_identifier(schema, quoted=True))
    return ValidatedSQL(
        tree.sql(dialect="postgres", unsupported_level=sqlglot.ErrorLevel.RAISE), limit
    )
