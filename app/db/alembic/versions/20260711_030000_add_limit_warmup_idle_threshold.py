"""add limit warm-up idle threshold setting

Revision ID: 20260711_030000_add_limit_warmup_idle_threshold
Revises: 20260711_020000_add_dashboard_account_concurrency_caps
Create Date: 2026-07-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260711_030000_add_limit_warmup_idle_threshold"
down_revision = "20260711_020000_add_dashboard_account_concurrency_caps"
branch_labels = None
depends_on = None

_COLUMN_NAME = "limit_warmup_idle_threshold_percent"


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
                server_default=sa.text("1.0"),
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if _COLUMN_NAME not in columns:
        return
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.drop_column(_COLUMN_NAME)
