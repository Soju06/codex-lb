"""Add live-row partial indexes for the unfiltered request-log facet skip scan.

The unfiltered ``GET /api/request-logs/options`` facets are computed with a
recursive ``facet_skip`` CTE: one ``min(column) WHERE deleted_at IS NULL AND
column > previous`` probe per distinct value. The existing facet indexes
(``idx_logs_model_effort_time``, ``idx_logs_status_error_time``,
``idx_logs_api_key_time``) carry soft-deleted rows too, so every probe walks
the whole soft-deleted cohort that shares a value before reaching the next
live one, and probe cost grows with the number of soft-deleted rows instead of
the number of distinct values. Partial indexes whose predicate matches the
probe's ``deleted_at IS NULL`` restore the bounded-probe contract. Account ids
need no live index: soft deletion detaches ``account_id`` (NULL), which an
``account_id > previous`` probe never walks.

PostgreSQL builds the indexes with ``CREATE INDEX CONCURRENTLY`` outside the
migration transaction so the proxy write path keeps inserting, and rebuilds a
leftover invalid index from an interrupted concurrent build instead of letting
``IF NOT EXISTS`` accept it by name. SQLite uses plain partial indexes.

Revision ID: 20260909_130000_add_request_logs_live_facet_indexes
Revises: 20260909_120000_dashboard_conversation_archive
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260909_130000_add_request_logs_live_facet_indexes"
down_revision = "20260909_120000_dashboard_conversation_archive"
branch_labels = None
depends_on = None

_TABLE_NAME = "request_logs"
_LIVE_ROW_PREDICATE = "deleted_at IS NULL"
_LIVE_FACET_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("idx_logs_live_api_key", ("api_key_id",)),
    ("idx_logs_live_model_effort", ("model", "reasoning_effort")),
    ("idx_logs_live_status_error", ("status", "error_code")),
)


def _drop_invalid_postgres_index(index_name: str) -> None:
    """Drop a leftover invalid index from an interrupted CREATE INDEX CONCURRENTLY.

    ``IF NOT EXISTS`` would silently accept the invalid index by name, leaving
    the facet probes without a usable live-row index.
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
            for index_name, columns in _LIVE_FACET_INDEXES:
                _drop_invalid_postgres_index(index_name)
                op.execute(
                    sa.text(
                        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} "
                        f"ON {_TABLE_NAME} ({', '.join(columns)}) WHERE {_LIVE_ROW_PREDICATE}"
                    )
                )
        return

    for index_name, columns in _LIVE_FACET_INDEXES:
        op.execute(
            sa.text(
                f"CREATE INDEX IF NOT EXISTS {index_name} "
                f"ON {_TABLE_NAME} ({', '.join(columns)}) WHERE {_LIVE_ROW_PREDICATE}"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for index_name, _columns in _LIVE_FACET_INDEXES:
                op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}"))
        return

    for index_name, _columns in _LIVE_FACET_INDEXES:
        op.drop_index(index_name, table_name=_TABLE_NAME, if_exists=True)
