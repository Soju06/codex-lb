from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

T = TypeVar("T")


def dialect_name(connection: Connection) -> str:
    return connection.dialect.name


def is_postgresql(connection: Connection) -> bool:
    return dialect_name(connection) == "postgresql"


def is_sqlite(connection: Connection) -> bool:
    return dialect_name(connection) == "sqlite"


def named_enum(
    connection: Connection,
    *values: str,
    name: str,
    create_type: bool = True,
) -> sa.Enum:
    if is_postgresql(connection):
        return postgresql.ENUM(*values, name=name, create_type=create_type)
    return sa.Enum(*values, name=name)


def create_named_enum(connection: Connection, *values: str, name: str) -> sa.Enum:
    enum_type = named_enum(connection, *values, name=name)
    if is_postgresql(connection):
        enum_type.create(connection, checkfirst=True)
    return enum_type


def dispatch_by_dialect(
    connection: Connection,
    *,
    default: Callable[[Connection], T] | None = None,
    **handlers: Callable[[Connection], T],
) -> T | None:
    handler = handlers.get(dialect_name(connection), default)
    if handler is None:
        return None
    return handler(connection)
