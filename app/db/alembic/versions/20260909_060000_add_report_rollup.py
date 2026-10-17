"""Add permanent report history; background fold owns the paced backfill."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260909_060000_add_report_rollup"
down_revision = "20260909_050000_dashboard_routing_overload_settings"
branch_labels = None
depends_on = None

_TABLE = "request_report_hourly_rollups"
_STATE_TABLE = "account_usage_rollup_state"
_WATERMARK_COLUMN = "reports_folded_through"


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
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("api_key_id", sa.String(), nullable=False),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("useragent_group", sa.String(), nullable=False),
            sa.Column("conversation_id", sa.String(), nullable=False),
            sa.Column("first_requested_at", sa.DateTime(), nullable=False),
            sa.Column("request_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("error_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("cancelled_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("input_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("output_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("reasoning_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("reasoning_usage_known_requests", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("cached_input_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
            sa.Column("cost_usd", sa.Float(), server_default=sa.text("0"), nullable=False),
            sa.PrimaryKeyConstraint(
                "bucket_epoch", "account_id", "api_key_id", "model", "useragent_group", "conversation_id"
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

    if _WATERMARK_COLUMN in _columns(bind, _STATE_TABLE):
        with op.batch_alter_table(_STATE_TABLE) as batch_op:
            batch_op.drop_column(_WATERMARK_COLUMN)

    if sa.inspect(bind).has_table(_TABLE):
        op.drop_table(_TABLE)
