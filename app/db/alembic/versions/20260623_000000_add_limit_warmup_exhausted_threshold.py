"""add limit warm-up exhausted threshold setting

Revision ID: 20260623_000000_add_limit_warmup_exhausted_threshold
Revises: 20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads
Create Date: 2026-06-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260623_000000_add_limit_warmup_exhausted_threshold"
down_revision = "20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads"
branch_labels = None
depends_on = None

_COLUMN_NAME = "limit_warmup_exhausted_threshold_percent"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns or _COLUMN_NAME in columns:
        return
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                _COLUMN_NAME,
                sa.Float(),
                nullable=False,
                server_default=sa.text("99.0"),
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if _COLUMN_NAME not in columns:
        return
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.drop_column(_COLUMN_NAME)
