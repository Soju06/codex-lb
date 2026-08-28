"""Add the normalized account-email lookup index.

Bundle preflight and commit match destination accounts case-insensitively via
``lower(accounts.email)``. The expression index keeps those probes indexed on
both supported database backends without changing stored email casing.

Revision ID: 20260828_010000_add_accounts_email_lower_index
Revises: 20260828_000000_add_accounts_chatgpt_identity_index
Create Date: 2026-08-28 01:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260828_010000_add_accounts_email_lower_index"
down_revision = "20260828_000000_add_accounts_chatgpt_identity_index"
branch_labels = None
depends_on = None

_INDEX = "idx_accounts_email_lower"
_TABLE = "accounts"


def _drop_invalid_postgres_index(index_name: str) -> None:
    """Remove an unusable remnant of interrupted concurrent index creation."""
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
            op.execute(sa.text(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX} ON {_TABLE} (lower(email))"))
    else:
        op.execute(sa.text(f"CREATE INDEX IF NOT EXISTS {_INDEX} ON {_TABLE} (lower(email))"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}"))
    else:
        op.drop_index(_INDEX, table_name=_TABLE, if_exists=True)
