"""Add dashboard_settings.local_login_policy (PLAN §4.6: who may use the local password form).

Revision ID: 20260911_030000_add_local_login_policy
Revises: 20260911_020000_add_http_bridge_terminal_append_phase
Create Date: 2026-09-10

String, NOT NULL, server default ``enabled``: an upgraded install keeps
today's behaviour (every active account holding a password may sign in) until
an operator tightens the policy from the Settings page. No data backfill, and
deliberately no environment variable.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260911_030000_add_local_login_policy"
down_revision = "20260911_020000_add_http_bridge_terminal_append_phase"
branch_labels = None
depends_on = None

_TABLE = "dashboard_settings"
_COLUMN = "local_login_policy"
_DEFAULT = "enabled"


def _columns(connection: Connection) -> set[str]:
    if not sa.inspect(connection).has_table(_TABLE):
        return set()
    return {column["name"] for column in sa.inspect(connection).get_columns(_TABLE)}


def upgrade() -> None:
    columns = _columns(op.get_bind())
    if columns and _COLUMN not in columns:
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.add_column(
                sa.Column(_COLUMN, sa.String(length=32), server_default=sa.text(f"'{_DEFAULT}'"), nullable=False)
            )


def downgrade() -> None:
    columns = _columns(op.get_bind())
    if columns and _COLUMN in columns:
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
