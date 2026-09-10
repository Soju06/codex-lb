"""stage the reset warm-up threshold for a mixed-version rollout

Revision ID: 20260909_130000_stage_limit_warmup_zero_compat
Revises: 20260909_070000_automation_run_claim_budget
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260909_130000_stage_limit_warmup_zero_compat"
down_revision = "20260909_070000_automation_run_claim_budget"
branch_labels = None
depends_on = None

_TABLE_NAME = "dashboard_settings"
_COLUMN_NAME = "limit_warmup_reset_threshold_percent"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    if _COLUMN_NAME in _columns(op.get_bind(), _TABLE_NAME):
        return
    with op.batch_alter_table(_TABLE_NAME) as batch_op:
        batch_op.add_column(
            sa.Column(
                _COLUMN_NAME,
                sa.Float(),
                nullable=True,
            )
        )


def downgrade() -> None:
    if _COLUMN_NAME not in _columns(op.get_bind(), _TABLE_NAME):
        return
    with op.batch_alter_table(_TABLE_NAME) as batch_op:
        batch_op.drop_column(_COLUMN_NAME)
