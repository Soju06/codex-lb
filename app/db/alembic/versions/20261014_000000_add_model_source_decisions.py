"""add model source decisions capabilities

Revision ID: 20261014_000000_add_model_source_decisions
Revises: 20260918_000000_merge_scim_and_overflow_heads
Create Date: 2026-10-14 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20261014_000000_add_model_source_decisions"
down_revision = "20260918_000000_merge_scim_and_overflow_heads"
branch_labels = None
depends_on = None

_CAPABILITY_COLUMNS = ("supports_decisions", "supports_systemone")


def _has_table(connection: Connection, table_name: str) -> bool:
    return sa.inspect(connection).has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    if not _has_table(connection, table_name):
        return set()
    return {column["name"] for column in sa.inspect(connection).get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    model_source_columns = _columns(bind, "model_sources")
    if not model_source_columns:
        return
    missing = [name for name in _CAPABILITY_COLUMNS if name not in model_source_columns]
    if not missing:
        return
    with op.batch_alter_table("model_sources") as batch_op:
        for name in missing:
            batch_op.add_column(
                sa.Column(
                    name,
                    sa.Boolean(),
                    server_default=sa.false(),
                    nullable=False,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    model_source_columns = _columns(bind, "model_sources")
    present = [name for name in _CAPABILITY_COLUMNS if name in model_source_columns]
    if not present:
        return
    with op.batch_alter_table("model_sources") as batch_op:
        for name in present:
            batch_op.drop_column(name)
