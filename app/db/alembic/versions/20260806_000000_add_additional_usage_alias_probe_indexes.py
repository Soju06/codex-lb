"""Add alias probe indexes for additional-usage latest-per-account reads.

The PostgreSQL latest-per-account read is being reshaped from a
``DISTINCT ON`` scan over every row under a quota key into correlated
per-account top-1 probes. Canonical probes ride the existing
``ix_additional_usage_quota_window_latest``; the registry's ``limit_name``
and ``metered_feature`` aliases match through ``lower(...)`` and therefore
need expression twins so each alias probe is a single btree descent too.

Revision ID: 20260806_000000_add_additional_usage_alias_probe_indexes
Revises: 20260804_230000_add_request_log_connection_request_kind
Create Date: 2026-08-06 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260806_000000_add_additional_usage_alias_probe_indexes"
down_revision = "20260804_230000_add_request_log_connection_request_kind"
branch_labels = None
depends_on = None

_ALIAS_INDEXES = (
    ("ix_additional_usage_alias_limit_latest", "lower(limit_name)"),
    ("ix_additional_usage_alias_feature_latest", "lower(metered_feature)"),
)


def _drop_invalid_postgres_index(index_name: str) -> None:
    """Drop a leftover invalid index from an interrupted CREATE INDEX CONCURRENTLY.

    ``IF NOT EXISTS`` would silently accept the invalid index by name,
    leaving the alias probes without a usable index.
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
            for index_name, expression in _ALIAS_INDEXES:
                _drop_invalid_postgres_index(index_name)
                op.execute(
                    sa.text(
                        f"""
                        CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name}
                        ON additional_usage_history (
                            {expression}, "window", account_id,
                            recorded_at DESC, used_percent DESC, id DESC
                        )
                        """
                    )
                )
    else:
        for index_name, expression in _ALIAS_INDEXES:
            op.execute(
                sa.text(
                    f"""
                    CREATE INDEX IF NOT EXISTS {index_name}
                    ON additional_usage_history (
                        {expression}, "window", account_id,
                        recorded_at DESC, used_percent DESC, id DESC
                    )
                    """
                )
            )


def downgrade() -> None:
    for index_name, _expression in _ALIAS_INDEXES:
        op.drop_index(index_name, table_name="additional_usage_history", if_exists=True)
