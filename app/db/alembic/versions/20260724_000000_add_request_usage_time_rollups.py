"""add request usage time rollup tables and hourly fold watermark

DDL only — the three tables are created empty and backfill is owned entirely
by the runtime fold pass, so this revision never blocks startup. The lifetime
rollups (`account_usage_rollups`, `api_key_usage_rollups`) and their
`folded_through` watermark are deliberately untouched: they cannot be
recomputed once retention has pruned raw request logs.

Revision ID: 20260724_000000_add_request_usage_time_rollups
Revises: 20260722_000000_backfill_request_log_useragent_families
Create Date: 2026-07-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260724_000000_add_request_usage_time_rollups"
down_revision = "20260722_000000_backfill_request_log_useragent_families"
branch_labels = None
depends_on = None

_HOURLY_TABLE = "request_usage_hourly_rollups"
_ERROR_TABLE = "request_usage_hourly_error_rollups"
_QUARTER_TABLE = "request_demand_quarter_rollups"
_STATE_TABLE = "account_usage_rollup_state"
_WATERMARK_COLUMN = "hourly_folded_through"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table(_HOURLY_TABLE):
        op.create_table(
            _HOURLY_TABLE,
            sa.Column("bucket_epoch", sa.BigInteger(), nullable=False),
            sa.Column("account_id", sa.String(), server_default=sa.text("''"), nullable=False),
            sa.Column("api_key_id", sa.String(), server_default=sa.text("''"), nullable=False),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("service_tier", sa.String(), server_default=sa.text("''"), nullable=False),
            sa.Column("request_kind", sa.String(), nullable=False),
            sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("request_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("error_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("input_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("output_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("reasoning_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("output_or_reasoning_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("cached_input_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("cached_input_tokens_clamped", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("cost_usd", sa.Float(), server_default=sa.text("0"), nullable=False),
            sa.Column("cost_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.PrimaryKeyConstraint(
                "bucket_epoch",
                "account_id",
                "api_key_id",
                "model",
                "service_tier",
                "request_kind",
                "is_deleted",
            ),
        )

    if not inspector.has_table(_ERROR_TABLE):
        op.create_table(
            _ERROR_TABLE,
            sa.Column("bucket_epoch", sa.BigInteger(), nullable=False),
            sa.Column("account_id", sa.String(), server_default=sa.text("''"), nullable=False),
            sa.Column("error_code", sa.String(), nullable=False),
            sa.Column("error_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.PrimaryKeyConstraint("bucket_epoch", "account_id", "error_code"),
        )

    # Pre-merge revisions of this (never released) migration created the
    # quarter table without the fine-grain planner dimensions. Rebuild it
    # empty and reset the hourly watermark so the fold repopulates all three
    # rollup tables from raw (re-folds converge via the fold pass's
    # defensive DELETE); such databases predate any release, so raw history
    # is still intact.
    quarter_columns = _columns(bind, _QUARTER_TABLE)
    if quarter_columns and "status" not in quarter_columns:
        op.drop_table(_QUARTER_TABLE)
        if _WATERMARK_COLUMN in _columns(bind, _STATE_TABLE):
            op.execute(sa.text(f"UPDATE {_STATE_TABLE} SET {_WATERMARK_COLUMN} = '1970-01-01 00:00:00'"))

    if not sa.inspect(bind).has_table(_QUARTER_TABLE):
        op.create_table(
            _QUARTER_TABLE,
            sa.Column("slot_epoch", sa.BigInteger(), nullable=False),
            sa.Column("account_id", sa.String(), server_default=sa.text("''"), nullable=False),
            sa.Column("api_key_id", sa.String(), server_default=sa.text("''"), nullable=False),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("reasoning_effort", sa.String(), server_default=sa.text("''"), nullable=False),
            sa.Column("request_kind", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("request_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("input_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("output_or_reasoning_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("cached_input_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("cost_usd", sa.Float(), server_default=sa.text("0"), nullable=False),
            sa.PrimaryKeyConstraint(
                "slot_epoch",
                "account_id",
                "api_key_id",
                "model",
                "reasoning_effort",
                "request_kind",
                "status",
                "is_deleted",
            ),
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
    inspector = sa.inspect(bind)

    state_columns = _columns(bind, _STATE_TABLE)
    if _WATERMARK_COLUMN in state_columns:
        with op.batch_alter_table(_STATE_TABLE) as batch_op:
            batch_op.drop_column(_WATERMARK_COLUMN)

    for table_name in (_QUARTER_TABLE, _ERROR_TABLE, _HOURLY_TABLE):
        if inspector.has_table(table_name):
            op.drop_table(table_name)
