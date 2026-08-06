"""add conversation presence rollup table and fold watermark

DDL only — the satellite is created empty and backfill is owned entirely by
the runtime conversation fold pass, so this revision never blocks startup.
The watermark column backfills existing state rows to the epoch, so the new
satellite starts its own paced backfill from zero without touching the
lifetime or hourly watermarks.

Revision ID: 20260806_010000_add_conversation_presence_rollup
Revises: 20260806_000000_add_additional_usage_alias_probe_indexes
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260806_010000_add_conversation_presence_rollup"
down_revision = "20260806_000000_add_additional_usage_alias_probe_indexes"
branch_labels = None
depends_on = None

_TABLE = "request_conversation_hourly_rollups"
_STATE_TABLE = "account_usage_rollup_state"
_WATERMARK_COLUMN = "conversation_folded_through"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()

    if not sa.inspect(bind).has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("bucket_epoch", sa.BigInteger(), nullable=False),
            sa.Column("conversation_id", sa.String(), nullable=False),
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("request_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.PrimaryKeyConstraint("bucket_epoch", "conversation_id", "account_id", "is_deleted"),
        )

    state_columns = _columns(bind, _STATE_TABLE)
    if state_columns and _WATERMARK_COLUMN not in state_columns:
        with op.batch_alter_table(_STATE_TABLE) as batch_op:
            batch_op.add_column(
                sa.Column(
                    _WATERMARK_COLUMN,
                    sa.DateTime(),
                    server_default=sa.text("'1970-01-01 00:00:00'"),
                    nullable=False,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()

    if _WATERMARK_COLUMN in _columns(bind, _STATE_TABLE):
        with op.batch_alter_table(_STATE_TABLE) as batch_op:
            batch_op.drop_column(_WATERMARK_COLUMN)

    if sa.inspect(bind).has_table(_TABLE):
        op.drop_table(_TABLE)
