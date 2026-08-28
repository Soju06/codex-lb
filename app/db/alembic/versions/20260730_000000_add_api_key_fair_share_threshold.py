"""add api key fair share threshold

Revision ID: 20260730_000000_add_api_key_fair_share_threshold
Revises: 20260806_010000_add_conversation_presence_rollup
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260730_000000_add_api_key_fair_share_threshold"
down_revision = "20260806_010000_add_conversation_presence_rollup"
branch_labels = None
depends_on = None

_COLUMN = "proxy_api_key_fair_share_congestion_threshold_pct"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    dashboard_columns = _columns(bind, "dashboard_settings")
    if not dashboard_columns or _COLUMN in dashboard_columns:
        return
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.add_column(sa.Column(_COLUMN, sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    dashboard_columns = _columns(bind, "dashboard_settings")
    if _COLUMN not in dashboard_columns:
        return
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.drop_column(_COLUMN)
