"""add dashboard guest access settings

Revision ID: 20260518_120000_add_dashboard_guest_access
Revises: 20260518_000000_add_http_bridge_durable_input_prefix
Create Date: 2026-05-18 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "20260518_120000_add_dashboard_guest_access"
down_revision: str | Sequence[str] | None = "20260518_000000_add_http_bridge_durable_input_prefix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "guest_access_enabled" not in columns:
            batch_op.add_column(
                sa.Column(
                    "guest_access_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if "guest_password_hash" not in columns:
            batch_op.add_column(sa.Column("guest_password_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "guest_password_hash" in columns:
            batch_op.drop_column("guest_password_hash")
        if "guest_access_enabled" in columns:
            batch_op.drop_column("guest_access_enabled")
