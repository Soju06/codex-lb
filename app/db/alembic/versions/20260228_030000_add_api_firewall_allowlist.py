"""add api_firewall_allowlist table

Revision ID: 20260228_030000_add_api_firewall_allowlist
Revises: 20260228_020000_align_api_key_limit_enum_types
Create Date: 2026-02-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260228_030000_add_api_firewall_allowlist"
down_revision = "20260228_020000_align_api_key_limit_enum_types"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "api_firewall_allowlist"):
        return

    op.create_table(
        "api_firewall_allowlist",
        sa.Column("ip_address", sa.String(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "api_firewall_allowlist"):
        op.drop_table("api_firewall_allowlist")
