"""add reset credit display settings

Revision ID: 20260716_010000_add_reset_credit_display_settings
Revises: 20260716_000000_add_oauth_device_flow_slots
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260716_010000_add_reset_credit_display_settings"
down_revision = "20260716_000000_add_oauth_device_flow_slots"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    dashboard_columns = _columns(bind, "dashboard_settings")
    if not dashboard_columns:
        return
    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "show_reset_credit_badges" not in dashboard_columns:
            batch_op.add_column(
                sa.Column(
                    "show_reset_credit_badges",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.true(),
                )
            )
        if "auto_redeem_reset_credits_before_expiry" not in dashboard_columns:
            batch_op.add_column(
                sa.Column(
                    "auto_redeem_reset_credits_before_expiry",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if "show_reset_credit_expiry_badge" not in dashboard_columns:
            batch_op.add_column(
                sa.Column(
                    "show_reset_credit_expiry_badge",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.true(),
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    dashboard_columns = _columns(bind, "dashboard_settings")
    if not dashboard_columns:
        return
    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "show_reset_credit_expiry_badge" in dashboard_columns:
            batch_op.drop_column("show_reset_credit_expiry_badge")
        if "auto_redeem_reset_credits_before_expiry" in dashboard_columns:
            batch_op.drop_column("auto_redeem_reset_credits_before_expiry")
        if "show_reset_credit_badges" in dashboard_columns:
            batch_op.drop_column("show_reset_credit_badges")
