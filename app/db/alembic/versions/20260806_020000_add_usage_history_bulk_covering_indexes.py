"""Add covering indexes for the projections bulk usage-history fetch.

The dashboard projections bulk read selects six narrow columns from
``usage_history`` but every index matching its predicate shapes is a pure
key index, so PostgreSQL fetches the heap for every matched row (avg 2.0 s
on the reference deployment). These covering twins carry the selected
columns in an ``INCLUDE`` payload so the read can be served index-only:
one for the coalesced-primary window shape and one for the explicit
raw-window shape (``secondary`` is a live dashboard path).

Non-PostgreSQL backends create the same-named indexes with key columns
only (``INCLUDE`` unsupported), mirroring ``20260717_000000``: SQLite
serves this read from its snapshot cache and never runs the SQL shape, so
the twins exist for schema/drift parity with the ``models.py``
declarations.

Revision ID: 20260806_020000_add_usage_history_bulk_covering_indexes
Revises: 20260730_000000_add_api_key_fair_share_threshold
Create Date: 2026-08-06 02:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260806_020000_add_usage_history_bulk_covering_indexes"
down_revision = "20260730_000000_add_api_key_fair_share_threshold"
branch_labels = None
depends_on = None

# The coalesced index carries the raw "window" column in its payload: the
# planner only considers an index-only scan when every column referenced by
# the query is returnable from the index, and expression key columns cannot
# return their underlying raw column, so without it the coalesce(...) qual
# disqualifies the index-only path (verified on PostgreSQL 18).
_COVERING_INDEXES = (
    (
        "idx_usage_window_account_time_covering",
        "coalesce(\"window\", 'primary')",
        'used_percent, reset_at, window_minutes, id, "window"',
    ),
    (
        "idx_usage_window_raw_account_time_covering",
        '"window"',
        "used_percent, reset_at, window_minutes, id",
    ),
)


def _drop_invalid_postgres_index(index_name: str) -> None:
    """Drop a leftover invalid index from an interrupted CREATE INDEX CONCURRENTLY.

    ``IF NOT EXISTS`` would silently accept the invalid index by name,
    leaving the bulk fetch without a usable covering index.
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
            for index_name, window_expression, include_columns in _COVERING_INDEXES:
                _drop_invalid_postgres_index(index_name)
                op.execute(
                    sa.text(
                        f"""
                        CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name}
                        ON usage_history ({window_expression}, account_id, recorded_at)
                        INCLUDE ({include_columns})
                        """
                    )
                )
    else:
        for index_name, window_expression, _include_columns in _COVERING_INDEXES:
            op.execute(
                sa.text(
                    f"""
                    CREATE INDEX IF NOT EXISTS {index_name}
                    ON usage_history ({window_expression}, account_id, recorded_at)
                    """
                )
            )


def downgrade() -> None:
    for index_name, _window_expression, _include_columns in _COVERING_INDEXES:
        op.drop_index(index_name, table_name="usage_history", if_exists=True)
