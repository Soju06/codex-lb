"""Add dashboard_settings.guest_session_generation for guest session revocation.

Revision ID: 20260908_000000_add_guest_session_generation
Revises: 20260910_010000_dashboard_spool_retention
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_000000_add_guest_session_generation"
down_revision = "20260910_010000_dashboard_spool_retention"
branch_labels = None
depends_on = None

_TABLE = "dashboard_settings"
_COLUMN = "guest_session_generation"


def _columns(bind: sa.engine.Connection) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind):
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.add_column(sa.Column(_COLUMN, sa.Integer(), server_default=sa.text("0"), nullable=False))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind):
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
