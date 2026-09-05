"""Add usage-history reset transition lookup index.

Rate-limit recovery probes explicit long windows for an old reset marker and a
later reset jump. Without a reset_at-leading key, PostgreSQL can walk the whole
cooldown tail for a still-blocked account on every scheduler cycle.

Revision ID: 20260904_000000_add_usage_reset_transition_index
Revises: 20260830_000000_add_quota_warmup_claim_expiry
Create Date: 2026-09-04 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260904_000000_add_usage_reset_transition_index"
down_revision = "20260830_000000_add_quota_warmup_claim_expiry"
branch_labels = None
depends_on = None

_INDEX_NAME = "idx_usage_window_account_reset_time"


def _drop_invalid_postgres_index(index_name: str) -> None:
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
            _drop_invalid_postgres_index(_INDEX_NAME)
            op.execute(
                sa.text(
                    f"""
                    CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX_NAME}
                    ON usage_history ("window", account_id, reset_at, recorded_at, id)
                    INCLUDE (used_percent, window_minutes)
                    """
                )
            )
        return

    op.execute(
        sa.text(
            f"""
            CREATE INDEX IF NOT EXISTS {_INDEX_NAME}
            ON usage_history ("window", account_id, reset_at, recorded_at, id)
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}"))
        return

    op.drop_index(_INDEX_NAME, table_name="usage_history", if_exists=True)
