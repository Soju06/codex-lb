"""Dialect-aware index DDL helpers for Alembic migrations (MySQL port branch).

SQLite and PostgreSQL keep their existing statements unchanged. These helpers
only supply the MySQL/MariaDB equivalents:

* MySQL has no ``CREATE INDEX IF NOT EXISTS``, so existence is checked through
  the SQLAlchemy inspector first;
* functional key parts must have their expression in parentheses;
* reserved identifiers (``key``, ``window``) must be backquoted — with the
  default ``sql_mode`` a double-quoted name would be a string literal;
* MySQL has no partial indexes, so a ``WHERE`` predicate is dropped and the
  full index is created instead (it still serves the same lookups).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Connection

#: Re-exported for the migration modules, which import it from here.
from app.db.dialect_sql import is_mysql


def index_exists(connection: Connection, index_name: str, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return False
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def create_mysql_index(
    connection: Connection,
    *,
    index_name: str,
    table_name: str,
    columns_sql: str,
    unique: bool = False,
) -> None:
    """Create ``index_name`` on ``table_name`` unless it already exists.

    ``columns_sql`` must already be written in MySQL syntax (backquoted
    identifiers, functional parts wrapped in parentheses).
    """
    if index_exists(connection, index_name, table_name):
        return
    keyword = "UNIQUE INDEX" if unique else "INDEX"
    connection.execute(sa.text(f"CREATE {keyword} {index_name} ON {table_name} ({columns_sql})"))


def text_default(connection: Connection, literal_sql: str) -> sa.TextClause:
    """A ``server_default`` for a TEXT column that MySQL accepts.

    MySQL rejects ``DEFAULT 'x'`` for TEXT/BLOB/JSON columns but accepts the
    expression form ``DEFAULT ('x')`` on 8.0.13+. SQLite and PostgreSQL keep
    the plain literal.
    """
    if is_mysql(connection):
        return sa.text(f"({literal_sql})")
    return sa.text(literal_sql)
