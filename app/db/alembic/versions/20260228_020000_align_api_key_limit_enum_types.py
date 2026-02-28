"""align api_key_limits enum column types on postgresql

Revision ID: 20260228_020000_align_api_key_limit_enum_types
Revises: 20260225_000000_add_dashboard_settings_routing_strategy
Create Date: 2026-02-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection
from sqlalchemy.sql.sqltypes import TypeEngine

# revision identifiers, used by Alembic.
revision = "20260228_020000_align_api_key_limit_enum_types"
down_revision = "20260225_000000_add_dashboard_settings_routing_strategy"
branch_labels = None
depends_on = None

_LIMIT_TYPE_VALUES = (
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "cost_usd",
)

_LIMIT_WINDOW_VALUES = (
    "daily",
    "weekly",
    "monthly",
)


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _column_types(connection: Connection, table_name: str) -> dict[str, TypeEngine]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return {}
    return {
        str(column["name"]): column["type"]
        for column in inspector.get_columns(table_name)
        if column.get("name") is not None and column.get("type") is not None
    }


def _is_named_postgresql_enum(column_type: TypeEngine, enum_name: str) -> bool:
    enum_values = getattr(column_type, "enums", None)
    if enum_values is None:
        return False
    return getattr(column_type, "name", None) == enum_name


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if not _table_exists(bind, "api_key_limits"):
        return

    limit_type_enum = sa.Enum(*_LIMIT_TYPE_VALUES, name="limit_type")
    limit_window_enum = sa.Enum(*_LIMIT_WINDOW_VALUES, name="limit_window")
    limit_type_enum.create(bind, checkfirst=True)
    limit_window_enum.create(bind, checkfirst=True)

    column_types = _column_types(bind, "api_key_limits")
    limit_type_column = column_types.get("limit_type")
    limit_window_column = column_types.get("limit_window")

    if limit_type_column is not None and not _is_named_postgresql_enum(limit_type_column, "limit_type"):
        op.execute(
            sa.text("ALTER TABLE api_key_limits ALTER COLUMN limit_type TYPE limit_type USING limit_type::limit_type")
        )
    if limit_window_column is not None and not _is_named_postgresql_enum(limit_window_column, "limit_window"):
        op.execute(
            sa.text(
                "ALTER TABLE api_key_limits "
                "ALTER COLUMN limit_window TYPE limit_window "
                "USING limit_window::limit_window"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if not _table_exists(bind, "api_key_limits"):
        return

    column_types = _column_types(bind, "api_key_limits")
    limit_type_column = column_types.get("limit_type")
    limit_window_column = column_types.get("limit_window")

    if limit_type_column is not None and _is_named_postgresql_enum(limit_type_column, "limit_type"):
        op.execute(sa.text("ALTER TABLE api_key_limits ALTER COLUMN limit_type TYPE VARCHAR USING limit_type::text"))
    if limit_window_column is not None and _is_named_postgresql_enum(limit_window_column, "limit_window"):
        op.execute(
            sa.text("ALTER TABLE api_key_limits ALTER COLUMN limit_window TYPE VARCHAR USING limit_window::text")
        )
