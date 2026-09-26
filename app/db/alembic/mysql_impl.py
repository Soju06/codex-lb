"""MySQL/MariaDB DDL behaviour for the ported schema (side branch).

Alembic's ``if_not_exists`` / ``if_exists`` flags render SQL that MySQL does
not accept (``CREATE INDEX IF NOT EXISTS``, ``DROP INDEX IF EXISTS ... ON``).
This module replaces the MySQL and MariaDB implementations in Alembic's
dialect registry with subclasses that check existence through the SQLAlchemy
inspector instead, so the same migrations run unchanged on MySQL.

SQLite and PostgreSQL keep Alembic's stock implementations; nothing here
affects them. Import this module before migrations run (``env.py`` does).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic.ddl.mysql import MariaDBImpl, MySQLImpl


class _IndexExistenceCompat:
    def _index_exists(self, index: Any) -> bool:
        table = getattr(index, "table", None)
        if table is None:
            return False
        inspector = sa.inspect(self.connection)  # type: ignore[attr-defined]
        schema = table.schema
        if not inspector.has_table(table.name, schema=schema):
            return False
        return any(existing.get("name") == index.name for existing in inspector.get_indexes(table.name, schema=schema))

    def create_index(self, index: Any, **kw: Any) -> None:
        if kw.pop("if_not_exists", False) and self._index_exists(index):
            return
        super().create_index(index, **kw)  # type: ignore[misc]

    def drop_index(self, index: Any, **kw: Any) -> None:
        if kw.pop("if_exists", False) and not self._index_exists(index):
            return
        super().drop_index(index, **kw)  # type: ignore[misc]


class CodexLbMySQLImpl(_IndexExistenceCompat, MySQLImpl):
    __dialect__ = "mysql"


class CodexLbMariaDBImpl(_IndexExistenceCompat, MariaDBImpl):
    __dialect__ = "mariadb"
