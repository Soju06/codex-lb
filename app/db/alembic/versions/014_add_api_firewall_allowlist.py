"""add api_firewall_allowlist table

Revision ID: 014_add_api_firewall_allowlist
Revises: 013_add_dashboard_settings_routing_strategy
Create Date: 2026-02-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "014_add_api_firewall_allowlist"
down_revision = "013_add_dashboard_settings_routing_strategy"
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
