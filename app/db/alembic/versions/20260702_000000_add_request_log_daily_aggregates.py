"""Add request log daily aggregates.

Revision ID: 20260702_000000_add_request_log_daily_aggregates
Revises: 20260626_010000_add_request_logs_upstream_transport
Create Date: 2026-07-02 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260702_000000_add_request_log_daily_aggregates"
down_revision = "20260626_010000_add_request_logs_upstream_transport"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if not _has_table("request_log_daily_aggregates"):
        op.create_table(
            "request_log_daily_aggregates",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("aggregate_key", sa.String(length=64), nullable=False),
            sa.Column("bucket_date", sa.Date(), nullable=False),
            sa.Column("api_key_id", sa.String(), nullable=True),
            sa.Column("account_id", sa.String(), nullable=True),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("error_code", sa.String(), nullable=True),
            sa.Column("request_kind", sa.String(), nullable=False),
            sa.Column("service_tier", sa.String(), nullable=True),
            sa.Column("requested_service_tier", sa.String(), nullable=True),
            sa.Column("actual_service_tier", sa.String(), nullable=True),
            sa.Column("transport", sa.String(), nullable=True),
            sa.Column("upstream_transport", sa.String(), nullable=True),
            sa.Column("source", sa.String(), nullable=True),
            sa.Column("useragent_group", sa.String(), nullable=True),
            sa.Column("plan_type", sa.String(), nullable=True),
            sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("request_count", sa.Integer(), nullable=False),
            sa.Column("error_count", sa.Integer(), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=False),
            sa.Column("output_tokens", sa.Integer(), nullable=False),
            sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
            sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
            sa.Column("cost_usd", sa.Float(), nullable=False),
            sa.Column("latency_ms_sum", sa.Integer(), nullable=False),
            sa.Column("latency_ms_count", sa.Integer(), nullable=False),
            sa.Column("latency_first_token_ms_sum", sa.Integer(), nullable=False),
            sa.Column("latency_first_token_ms_count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("aggregate_key"),
        )

    if not _has_index("request_log_daily_aggregates", "idx_request_log_daily_aggregates_date_key"):
        op.create_index(
            "idx_request_log_daily_aggregates_date_key",
            "request_log_daily_aggregates",
            ["bucket_date", "api_key_id"],
        )
    if not _has_index("request_log_daily_aggregates", "idx_request_log_daily_aggregates_account_date"):
        op.create_index(
            "idx_request_log_daily_aggregates_account_date",
            "request_log_daily_aggregates",
            ["account_id", "bucket_date"],
        )


def downgrade() -> None:
    if not _has_table("request_log_daily_aggregates"):
        return
    if _has_index("request_log_daily_aggregates", "idx_request_log_daily_aggregates_account_date"):
        op.drop_index("idx_request_log_daily_aggregates_account_date", table_name="request_log_daily_aggregates")
    if _has_index("request_log_daily_aggregates", "idx_request_log_daily_aggregates_date_key"):
        op.drop_index("idx_request_log_daily_aggregates_date_key", table_name="request_log_daily_aggregates")
    op.drop_table("request_log_daily_aggregates")
