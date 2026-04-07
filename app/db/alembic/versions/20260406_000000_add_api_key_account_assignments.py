"""add api key account assignments

Revision ID: 20260406_000000_add_api_key_account_assignments
Revises: 20260403_000000_add_credit_api_key_limit_values
Create Date: 2026-04-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260406_000000_add_api_key_account_assignments"
down_revision = "20260403_000000_add_credit_api_key_limit_values"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _indexes(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "api_key_accounts"):
        op.create_table(
            "api_key_accounts",
            sa.Column("api_key_id", sa.String(), sa.ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False),
            sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("api_key_id", "account_id"),
        )

    existing_indexes = _indexes(bind, "api_key_accounts")
    if "idx_api_key_accounts_account_id" not in existing_indexes:
        op.create_index("idx_api_key_accounts_account_id", "api_key_accounts", ["account_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "api_key_accounts"):
        return

    existing_indexes = _indexes(bind, "api_key_accounts")
    if "idx_api_key_accounts_account_id" in existing_indexes:
        op.drop_index("idx_api_key_accounts_account_id", table_name="api_key_accounts")
    op.drop_table("api_key_accounts")
