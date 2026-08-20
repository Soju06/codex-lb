"""add model source embeddings capability

Revision ID: 20260816_000000_add_model_source_embeddings
Revises: 20260806_030000_add_api_key_allowed_reasoning_efforts
Create Date: 2026-08-16 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260816_000000_add_model_source_embeddings"
down_revision = "20260806_030000_add_api_key_allowed_reasoning_efforts"
branch_labels = None
depends_on = None


def _has_table(connection: Connection, table_name: str) -> bool:
    return sa.inspect(connection).has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    if not _has_table(connection, table_name):
        return set()
    return {column["name"] for column in sa.inspect(connection).get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    model_source_columns = _columns(bind, "model_sources")
    if model_source_columns and "supports_embeddings" not in model_source_columns:
        with op.batch_alter_table("model_sources") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "supports_embeddings",
                    sa.Boolean(),
                    server_default=sa.false(),
                    nullable=False,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    model_source_columns = _columns(bind, "model_sources")
    if "supports_embeddings" in model_source_columns:
        with op.batch_alter_table("model_sources") as batch_op:
            batch_op.drop_column("supports_embeddings")
