"""add replica guardrails (settings version + runtime sentinels)

Revision ID: 20260713_040000_add_replica_guardrails
Revises: 20260712_020000_add_api_key_usage_rollups
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260713_040000_add_replica_guardrails"
down_revision = "20260712_020000_add_api_key_usage_rollups"
branch_labels = None
depends_on = None

_VERSION_COLUMN = "version"
_SENTINELS_TABLE = "runtime_sentinels"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def _has_table(connection: Connection, table_name: str) -> bool:
    return sa.inspect(connection).has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if columns and _VERSION_COLUMN not in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    _VERSION_COLUMN,
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("1"),
                )
            )
    if not _has_table(bind, _SENTINELS_TABLE):
        op.create_table(
            _SENTINELS_TABLE,
            sa.Column("name", sa.String(length=64), primary_key=True),
            sa.Column("value", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _SENTINELS_TABLE):
        op.drop_table(_SENTINELS_TABLE)
    columns = _columns(bind, "dashboard_settings")
    if _VERSION_COLUMN in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.drop_column(_VERSION_COLUMN)
