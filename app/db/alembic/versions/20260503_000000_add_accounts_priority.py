"""add priority to accounts

Revision ID: 20260503_000000_add_accounts_priority
Revises: 20260424_000000_merge_dashboard_session_ttl_and_request_log_heads
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260503_000000_add_accounts_priority"
down_revision = "20260424_000000_merge_dashboard_session_ttl_and_request_log_heads"
branch_labels = None
depends_on = None

_ACCOUNT_PRIORITY = sa.Enum(
    "gold",
    "silver",
    "bronze",
    name="account_priority",
)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "accounts")
    if not columns or "priority" in columns:
        return

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "priority",
                _ACCOUNT_PRIORITY,
                nullable=False,
                server_default=sa.text("'silver'"),
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "accounts")
    if "priority" not in columns:
        return

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_column("priority")

    _ACCOUNT_PRIORITY.drop(bind, checkfirst=True)
