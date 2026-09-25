"""Dialect-aware index DDL helpers for Alembic migrations (MySQL port branch).

SQLite and PostgreSQL keep their existing statements unchanged. These helpers
only supply the MySQL/MariaDB equivalents:

* MySQL has no ``CREATE INDEX IF NOT EXISTS``, so existence is checked through
  the SQLAlchemy inspector first;
* MySQL indexes a functional key part by wrapping the expression in
  parentheses; MariaDB cannot index an expression at all, so the same key part
  becomes a virtual generated column that the index references instead
  (``GeneratedKeyPart``). MariaDB's optimiser uses an index on a virtual column
  for the same predicates, so the index keeps serving the query it was added
  for;
* reserved identifiers (``key``, ``window``) must be backquoted — with the
  default ``sql_mode`` a double-quoted name would be a string literal;
* MySQL has no partial indexes, so a ``WHERE`` predicate is dropped and the
  full index is created instead (it still serves the same lookups).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Connection

#: Re-exported for the migration modules, which import them from here.
from app.db.dialect_sql import is_mariadb, is_mysql

__all__ = [
    "GeneratedKeyPart",
    "create_mysql_index",
    "index_exists",
    "is_mariadb",
    "is_mysql",
    "text_default",
]


@dataclass(frozen=True)
class GeneratedKeyPart:
    """A functional key part for indexes MariaDB cannot express directly.

    ``placeholder`` is the token the caller writes into ``columns_sql`` (for
    example ``"{window_key}, `account_id`"``). MySQL renders it as the
    parenthesised expression its functional indexes require; MariaDB gets a
    ``VIRTUAL`` generated column holding the expression and the index
    references that column.
    """

    placeholder: str
    column: str
    expression_sql: str
    column_type_sql: str


def index_exists(connection: Connection, index_name: str, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return False
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _ensure_generated_column(connection: Connection, *, table_name: str, part: GeneratedKeyPart) -> None:
    """Create the MariaDB virtual column that backs a functional key part."""
    inspector = sa.inspect(connection)
    if any(column.get("name") == part.column for column in inspector.get_columns(table_name)):
        return
    connection.execute(
        sa.text(
            f"ALTER TABLE {table_name} ADD COLUMN `{part.column}` {part.column_type_sql} "
            f"AS ({part.expression_sql}) VIRTUAL"
        )
    )


def _render_columns(
    connection: Connection,
    *,
    table_name: str,
    columns_sql: str,
    generated_parts: Sequence[GeneratedKeyPart],
) -> str:
    rendered = columns_sql
    mariadb = is_mariadb(connection)
    for part in generated_parts:
        if mariadb:
            _ensure_generated_column(connection, table_name=table_name, part=part)
            replacement = f"`{part.column}`"
        else:
            replacement = f"(({part.expression_sql}))"
        rendered = rendered.replace("{" + part.placeholder + "}", replacement)
    return rendered


def create_mysql_index(
    connection: Connection,
    *,
    index_name: str,
    table_name: str,
    columns_sql: str,
    unique: bool = False,
    generated_parts: Sequence[GeneratedKeyPart] = (),
) -> None:
    """Create ``index_name`` on ``table_name`` unless it already exists.

    ``columns_sql`` must already be written in MySQL syntax (backquoted
    identifiers, functional parts wrapped in parentheses). A functional key
    part is written as the ``{placeholder}`` of a ``GeneratedKeyPart`` instead,
    so that MariaDB can index it through a virtual generated column.
    """
    if index_exists(connection, index_name, table_name):
        return
    columns = _render_columns(
        connection,
        table_name=table_name,
        columns_sql=columns_sql,
        generated_parts=generated_parts,
    )
    keyword = "UNIQUE INDEX" if unique else "INDEX"
    connection.execute(sa.text(f"CREATE {keyword} {index_name} ON {table_name} ({columns})"))


def text_default(connection: Connection, literal_sql: str) -> sa.TextClause:
    """A ``server_default`` for a TEXT column that MySQL accepts.

    MySQL rejects ``DEFAULT 'x'`` for TEXT/BLOB/JSON columns but accepts the
    expression form ``DEFAULT ('x')`` on 8.0.13+. SQLite and PostgreSQL keep
    the plain literal.
    """
    if is_mysql(connection):
        return sa.text(f"({literal_sql})")
    return sa.text(literal_sql)
