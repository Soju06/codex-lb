"""add reset credit redeem coordination tables

Revision ID: 20260713_070000_add_reset_credit_redeem_tables
Revises: 20260712_020000_add_api_key_usage_rollups
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260713_070000_add_reset_credit_redeem_tables"
down_revision = "20260713_040000_add_replica_guardrails"
branch_labels = None
depends_on = None

_REQUESTS_TABLE = "reset_credit_redeem_requests"
_CLAIMS_TABLE = "reset_credit_redeem_claims"
_REQUESTS_CREATED_AT_INDEX = "ix_reset_credit_redeem_requests_created_at"


def _has_table(connection: Connection, table_name: str) -> bool:
    return sa.inspect(connection).has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _REQUESTS_TABLE):
        op.create_table(
            _REQUESTS_TABLE,
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("redeem_request_id", sa.String(), nullable=False),
            sa.Column("credit_id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("account_id", "redeem_request_id"),
        )
        op.create_index(_REQUESTS_CREATED_AT_INDEX, _REQUESTS_TABLE, ["created_at"])
    if not _has_table(bind, _CLAIMS_TABLE):
        op.create_table(
            _CLAIMS_TABLE,
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("holder_id", sa.String(length=100), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("account_id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _CLAIMS_TABLE):
        op.drop_table(_CLAIMS_TABLE)
    if _has_table(bind, _REQUESTS_TABLE):
        op.drop_index(_REQUESTS_CREATED_AT_INDEX, table_name=_REQUESTS_TABLE)
        op.drop_table(_REQUESTS_TABLE)
