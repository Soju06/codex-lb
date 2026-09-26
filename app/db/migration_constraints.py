"""Dialect-aware constraint DDL helpers for Alembic migrations (MySQL port branch).

MariaDB refuses to change a column that a foreign key uses, even with
``foreign_key_checks = 0``::

    ERROR 1832: Cannot change column 'account_id': used in a foreign key
    constraint 'request_logs_ibfk_1'

MySQL performs the same ``ALTER TABLE ... MODIFY`` happily, so only the MariaDB
path has to take the constraint out of the way. ``mariadb_fk_safe_alter`` wraps
an Alembic operation: on MariaDB it drops every foreign key that uses the
column, runs the wrapped statements, and re-adds the constraints with their
original referential actions. Everywhere else it is a transparent wrapper.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from app.db.migration_indexes import is_mariadb

__all__ = [
    "ForeignKeyDefinition",
    "foreign_keys_using_column",
    "mariadb_fk_safe_alter",
]


@dataclass(frozen=True)
class ForeignKeyDefinition:
    """A foreign key of one table, as ``information_schema`` reports it."""

    name: str
    columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...]
    on_update: str
    on_delete: str

    def add_sql(self, table_name: str) -> str:
        columns = ", ".join(f"`{column}`" for column in self.columns)
        referenced = ", ".join(f"`{column}`" for column in self.referenced_columns)
        return (
            f"ALTER TABLE {table_name} ADD CONSTRAINT `{self.name}` "
            f"FOREIGN KEY ({columns}) REFERENCES `{self.referenced_table}` ({referenced}) "
            f"ON DELETE {self.on_delete} ON UPDATE {self.on_update}"
        )


def foreign_keys_using_column(
    connection: Connection,
    *,
    table_name: str,
    column_name: str,
) -> tuple[ForeignKeyDefinition, ...]:
    """Foreign keys of ``table_name`` whose child side includes ``column_name``."""
    rows = connection.execute(
        sa.text(
            """
            SELECT kcu.constraint_name,
                   kcu.column_name,
                   kcu.referenced_table_name,
                   kcu.referenced_column_name,
                   rc.update_rule,
                   rc.delete_rule
            FROM information_schema.key_column_usage AS kcu
            JOIN information_schema.referential_constraints AS rc
              ON rc.constraint_schema = kcu.constraint_schema
             AND rc.constraint_name = kcu.constraint_name
            WHERE kcu.table_schema = DATABASE()
              AND kcu.table_name = :table_name
              AND kcu.referenced_table_name IS NOT NULL
            ORDER BY kcu.constraint_name, kcu.ordinal_position
            """
        ),
        {"table_name": table_name},
    ).fetchall()

    grouped: dict[str, dict[str, object]] = {}
    for constraint_name, column, referenced_table, referenced_column, update_rule, delete_rule in rows:
        entry = grouped.setdefault(
            str(constraint_name),
            {
                "columns": [],
                "referenced_table": str(referenced_table),
                "referenced_columns": [],
                "on_update": str(update_rule),
                "on_delete": str(delete_rule),
            },
        )
        cast_columns = entry["columns"]
        cast_referenced = entry["referenced_columns"]
        assert isinstance(cast_columns, list) and isinstance(cast_referenced, list)
        cast_columns.append(str(column))
        cast_referenced.append(str(referenced_column))

    definitions = []
    for name, entry in grouped.items():
        columns = entry["columns"]
        referenced_columns = entry["referenced_columns"]
        if column_name not in columns:
            continue
        definitions.append(
            ForeignKeyDefinition(
                name=name,
                columns=tuple(columns),  # type: ignore[arg-type]
                referenced_table=str(entry["referenced_table"]),
                referenced_columns=tuple(referenced_columns),  # type: ignore[arg-type]
                on_update=str(entry["on_update"]),
                on_delete=str(entry["on_delete"]),
            )
        )
    return tuple(definitions)


@contextmanager
def mariadb_fk_safe_alter(
    connection: Connection,
    *,
    table_name: str,
    column_name: str,
) -> Iterator[None]:
    """Run the wrapped statements with the column's foreign keys out of the way.

    A no-op on every dialect but MariaDB, which refuses to modify a column used
    by a foreign key. The constraints are re-added with their original
    referential actions once the wrapped statements finish (or fail).
    """
    if not is_mariadb(connection):
        yield
        return

    definitions = foreign_keys_using_column(connection, table_name=table_name, column_name=column_name)
    for definition in definitions:
        connection.execute(sa.text(f"ALTER TABLE {table_name} DROP FOREIGN KEY `{definition.name}`"))
    try:
        yield
    finally:
        for definition in definitions:
            connection.execute(sa.text(definition.add_sql(table_name)))
