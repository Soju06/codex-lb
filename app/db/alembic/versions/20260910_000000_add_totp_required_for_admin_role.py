"""Add dashboard_settings.totp_required_for_admin_role (D9: TOTP required for admin-level accounts).

Revision ID: 20260910_000000_add_totp_required_for_admin_role
Revises: 20260909_040000_add_dashboard_user_invites
Create Date: 2026-09-10

Boolean, NOT NULL, server default false: existing installs keep today's
behaviour (only the global ``totp_required_on_login`` toggle applies) until an
admin turns the new option on from the Settings page. No data backfill.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260910_000000_add_totp_required_for_admin_role"
down_revision = "20260909_040000_add_dashboard_user_invites"
branch_labels = None
depends_on = None

_TABLE = "dashboard_settings"
_COLUMN = "totp_required_for_admin_role"


def _columns(connection: Connection) -> set[str]:
    if not sa.inspect(connection).has_table(_TABLE):
        return set()
    return {column["name"] for column in sa.inspect(connection).get_columns(_TABLE)}


def upgrade() -> None:
    columns = _columns(op.get_bind())
    if columns and _COLUMN not in columns:
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.add_column(sa.Column(_COLUMN, sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    columns = _columns(op.get_bind())
    if columns and _COLUMN in columns:
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
