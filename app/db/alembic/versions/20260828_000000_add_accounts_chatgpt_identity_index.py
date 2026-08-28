"""add account ChatGPT identity lookup index

Speeds up the per-snapshot ``chatgpt_account_id`` lookups performed by
``UsageRepository.settle_live_account_snapshot`` when it resolves live
usage snapshot owners.

Revision ID: 20260828_000000_add_accounts_chatgpt_identity_index
Revises: 20260826_000000_add_http_bridge_event_chunks
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260828_000000_add_accounts_chatgpt_identity_index"
down_revision = "20260826_000000_add_http_bridge_event_chunks"
branch_labels = None
depends_on = None

_INDEX = "idx_accounts_chatgpt_account_id"
_TABLE = "accounts"


def _drop_invalid_postgres_index(index_name: str) -> None:
    """Drop a leftover invalid index from an interrupted CREATE INDEX CONCURRENTLY.

    ``IF NOT EXISTS`` would silently accept the invalid index by name,
    stamping the revision without a usable identity index.
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
            _drop_invalid_postgres_index(_INDEX)
            op.execute(sa.text(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX} ON {_TABLE} (chatgpt_account_id)"))
    else:
        op.execute(sa.text(f"CREATE INDEX IF NOT EXISTS {_INDEX} ON {_TABLE} (chatgpt_account_id)"))


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE, if_exists=True)
