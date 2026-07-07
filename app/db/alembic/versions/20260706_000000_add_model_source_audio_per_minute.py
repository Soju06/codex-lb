"""add model source audio per-minute pricing

Revision ID: 20260706_000000_add_model_source_audio_per_minute
Revises: 20260705_000000_add_model_source_audio_transcriptions
Create Date: 2026-07-06 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260706_000000_add_model_source_audio_per_minute"
down_revision = "20260705_000000_add_model_source_audio_transcriptions"
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
    model_columns = _columns(bind, "model_source_models")
    if model_columns and "audio_per_minute" not in model_columns:
        with op.batch_alter_table("model_source_models") as batch_op:
            batch_op.add_column(sa.Column("audio_per_minute", sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    model_columns = _columns(bind, "model_source_models")
    if "audio_per_minute" in model_columns:
        with op.batch_alter_table("model_source_models") as batch_op:
            batch_op.drop_column("audio_per_minute")
