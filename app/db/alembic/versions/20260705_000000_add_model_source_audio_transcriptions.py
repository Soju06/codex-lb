"""add model source audio transcription capability

Revision ID: 20260705_000000_add_model_source_audio_transcriptions
Revises: 20260702_000000_add_model_sources
Create Date: 2026-07-05 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260705_000000_add_model_source_audio_transcriptions"
down_revision = "20260702_000000_add_model_sources"
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
    if model_source_columns and "supports_audio_transcriptions" not in model_source_columns:
        with op.batch_alter_table("model_sources") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "supports_audio_transcriptions",
                    sa.Boolean(),
                    server_default=sa.false(),
                    nullable=False,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    model_source_columns = _columns(bind, "model_sources")
    if "supports_audio_transcriptions" in model_source_columns:
        with op.batch_alter_table("model_sources") as batch_op:
            batch_op.drop_column("supports_audio_transcriptions")
