"""activate the reset warm-up threshold with an opt-in zero default

Revision ID: 20260906_000000_restore_limit_warmup_threshold_default
Revises: 20260909_130000_stage_limit_warmup_zero_compat
Create Date: 2026-09-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260906_000000_restore_limit_warmup_threshold_default"
down_revision = "20260909_130000_stage_limit_warmup_zero_compat"
branch_labels = None
depends_on = None

_ACTIVE_COLUMN_NAME = "limit_warmup_reset_threshold_percent"
_NEW_DEFAULT = 0.0


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    if _ACTIVE_COLUMN_NAME not in _columns(bind, "dashboard_settings"):
        return

    op.execute(
        sa.text(
            "UPDATE dashboard_settings SET limit_warmup_reset_threshold_percent = :default "
            "WHERE limit_warmup_reset_threshold_percent IS NULL"
        ).bindparams(default=_NEW_DEFAULT)
    )
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.alter_column(
            _ACTIVE_COLUMN_NAME,
            existing_type=sa.Float(),
            nullable=False,
            server_default=sa.text(str(_NEW_DEFAULT)),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _ACTIVE_COLUMN_NAME not in _columns(bind, "dashboard_settings"):
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.alter_column(
            _ACTIVE_COLUMN_NAME,
            existing_type=sa.Float(),
            nullable=True,
            server_default=None,
        )
