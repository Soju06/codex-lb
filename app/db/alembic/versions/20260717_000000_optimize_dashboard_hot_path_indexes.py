"""Optimize dashboard hot-path indexes for request_logs and additional_usage_history.

Adds a covering partial index so the quota-planner slot aggregation over
``request_logs`` can be satisfied with an index-only scan, adds a composite
index for the recurring distinct quota-label lookup over
``additional_usage_history``, and drops indexes that are strict-prefix
duplicates of wider indexes on the same tables.

``idx_logs_request_status_api_key_time`` is intentionally kept: the
sessionless response-owner fallback lookup orders by
``requested_at DESC, id DESC`` after an equality prefix, and the wider
session-scoped index cannot return that order because ``session_id`` sits
between the prefix and the ordering columns.

On PostgreSQL this also tightens per-table autovacuum settings for the two
insert-heavy tables so the visibility map stays fresh enough for index-only
scans even after crash recovery resets the cumulative statistics counters.

Revision ID: 20260717_000000_optimize_dashboard_hot_path_indexes
Revises: 20260717_000000_merge_retention_and_reset_credit_display_heads
Create Date: 2026-07-17 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260717_000000_optimize_dashboard_hot_path_indexes"
down_revision = "20260717_000000_merge_retention_and_reset_credit_display_heads"
branch_labels = None
depends_on = None

_COVERING_INDEX_NAME = "idx_logs_dash_usage_covering"
_LABELS_INDEX_NAME = "ix_additional_usage_distinct_labels"

_POSTGRES_AUTOVACUUM_SETTINGS = (
    "autovacuum_vacuum_insert_scale_factor = 0.02, "
    "autovacuum_vacuum_insert_threshold = 50000, "
    "autovacuum_analyze_scale_factor = 0.02"
)
_POSTGRES_AUTOVACUUM_RESET = (
    "autovacuum_vacuum_insert_scale_factor, autovacuum_vacuum_insert_threshold, autovacuum_analyze_scale_factor"
)
_AUTOVACUUM_TABLES = ("request_logs", "additional_usage_history")

_REDUNDANT_INDEXES = (
    ("idx_logs_requested_at", "request_logs"),
    ("idx_logs_api_key_time_account", "request_logs"),
    ("ix_additional_usage_history_account_id", "additional_usage_history"),
)


def _drop_invalid_postgres_index(index_name: str) -> None:
    """Drop a leftover invalid index from an interrupted CREATE INDEX CONCURRENTLY.

    ``IF NOT EXISTS`` would silently accept the invalid index by name, leaving
    the hot path without a usable index after the redundant ones are dropped.
    """
    bind = op.get_bind()
    invalid = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE c.relname = :name AND NOT i.indisvalid"
        ),
        {"name": index_name},
    ).scalar()
    if invalid:
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}"))


def upgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            _drop_invalid_postgres_index(_COVERING_INDEX_NAME)
            op.execute(
                sa.text(
                    f"""
                    CREATE INDEX CONCURRENTLY IF NOT EXISTS {_COVERING_INDEX_NAME}
                    ON request_logs (requested_at)
                    INCLUDE (
                        account_id, api_key_id, model, reasoning_effort, request_kind,
                        status, input_tokens, cached_input_tokens, output_tokens,
                        reasoning_tokens, cost_usd, id
                    )
                    WHERE deleted_at IS NULL
                    """
                )
            )
            _drop_invalid_postgres_index(_LABELS_INDEX_NAME)
            op.execute(
                sa.text(
                    f"""
                    CREATE INDEX CONCURRENTLY IF NOT EXISTS {_LABELS_INDEX_NAME}
                    ON additional_usage_history (account_id, quota_key, limit_name, metered_feature)
                    """
                )
            )
    else:
        op.execute(
            sa.text(
                f"""
                CREATE INDEX IF NOT EXISTS {_COVERING_INDEX_NAME}
                ON request_logs (requested_at)
                WHERE deleted_at IS NULL
                """
            )
        )
        op.create_index(
            _LABELS_INDEX_NAME,
            "additional_usage_history",
            ["account_id", "quota_key", "limit_name", "metered_feature"],
            unique=False,
            if_not_exists=True,
        )

    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for index_name, _table_name in _REDUNDANT_INDEXES:
                op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}"))
    else:
        for index_name, table_name in _REDUNDANT_INDEXES:
            op.drop_index(index_name, table_name=table_name, if_exists=True)

    if bind.dialect.name == "postgresql":
        for table_name in _AUTOVACUUM_TABLES:
            op.execute(sa.text(f"ALTER TABLE {table_name} SET ({_POSTGRES_AUTOVACUUM_SETTINGS})"))


def downgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        for table_name in _AUTOVACUUM_TABLES:
            op.execute(sa.text(f"ALTER TABLE {table_name} RESET ({_POSTGRES_AUTOVACUUM_RESET})"))

    op.create_index(
        "ix_additional_usage_history_account_id",
        "additional_usage_history",
        ["account_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_logs_api_key_time_account",
        "request_logs",
        ["api_key_id", sa.text("requested_at DESC"), "account_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "idx_logs_requested_at",
        "request_logs",
        ["requested_at"],
        unique=False,
        if_not_exists=True,
    )

    op.drop_index(_LABELS_INDEX_NAME, table_name="additional_usage_history", if_exists=True)
    op.drop_index(_COVERING_INDEX_NAME, table_name="request_logs", if_exists=True)
