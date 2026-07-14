"""Harden request log rollup parity.

Revision ID: 20260714_000000_harden_request_log_rollup_parity
Revises: 20260711_000000_merge_daily_aggregates_and_ttft_heads
Create Date: 2026-07-14 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260714_000000_harden_request_log_rollup_parity"
down_revision = "20260711_000000_merge_daily_aggregates_and_ttft_heads"
branch_labels = None
depends_on = None


def _has_column(column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in {column["name"] for column in inspector.get_columns("request_log_daily_aggregates")}


def upgrade() -> None:
    columns = (
        (
            sa.Column("effective_output_tokens", sa.Integer(), server_default="0", nullable=False),
            "output_tokens",
        ),
        (
            sa.Column("cost_microdollars", sa.BigInteger(), server_default="0", nullable=False),
            "CAST(cost_usd * 1000000 AS BIGINT)",
        ),
        (
            sa.Column("account_request_count", sa.Integer(), server_default="0", nullable=False),
            "request_count",
        ),
        (
            sa.Column("account_input_tokens", sa.Integer(), server_default="0", nullable=False),
            "input_tokens",
        ),
        (
            sa.Column("account_output_tokens", sa.Integer(), server_default="0", nullable=False),
            "output_tokens",
        ),
        (
            sa.Column("account_cached_input_tokens", sa.Integer(), server_default="0", nullable=False),
            "cached_input_tokens",
        ),
        (
            sa.Column("account_cost_usd", sa.Float(), server_default="0", nullable=False),
            "cost_usd",
        ),
    )
    for column, backfill_expression in columns:
        if _has_column(column.name):
            continue
        op.add_column("request_log_daily_aggregates", column)
        op.execute(sa.text(f"UPDATE request_log_daily_aggregates SET {column.name} = {backfill_expression}"))


def downgrade() -> None:
    for column_name in (
        "account_cost_usd",
        "account_cached_input_tokens",
        "account_output_tokens",
        "account_input_tokens",
        "account_request_count",
        "effective_output_tokens",
        "cost_microdollars",
    ):
        if _has_column(column_name):
            op.drop_column("request_log_daily_aggregates", column_name)
