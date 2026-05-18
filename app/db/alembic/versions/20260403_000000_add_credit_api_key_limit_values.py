"""add credit-based api key limit enum values

Revision ID: 20260403_000000_add_credit_api_key_limit_values
Revises: 20260402_000000_switch_dashboard_routing_default_to_capacity_weighted
Create Date: 2026-04-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260403_000000_add_credit_api_key_limit_values"
down_revision = "20260402_000000_switch_dashboard_routing_default_to_capacity_weighted"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "api_key_limits"):
        return
    if bind.dialect.name != "postgresql":
        return

    op.execute(sa.text("ALTER TYPE limit_type ADD VALUE IF NOT EXISTS 'credits'"))
    op.execute(sa.text("ALTER TYPE limit_window ADD VALUE IF NOT EXISTS '5h'"))
    op.execute(sa.text("ALTER TYPE limit_window ADD VALUE IF NOT EXISTS '7d'"))


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "api_key_limits"):
        return
    if bind.dialect.name != "postgresql":
        return
    # PostgreSQL enum values cannot be removed safely in-place here.
    return
