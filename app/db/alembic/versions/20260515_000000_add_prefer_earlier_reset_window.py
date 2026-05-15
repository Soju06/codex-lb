"""add prefer earlier reset window setting

Revision ID: 20260515_000000_add_prefer_earlier_reset_window
Revises: 20260514_000000_add_request_logs_api_key_time_index
Create Date: 2026-05-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260515_000000_add_prefer_earlier_reset_window"
down_revision = "20260514_000000_add_request_logs_api_key_time_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("dashboard_settings"):
        return

    columns = {column["name"] for column in inspector.get_columns("dashboard_settings")}
    if "prefer_earlier_reset_window" not in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "prefer_earlier_reset_window",
                    sa.String(),
                    nullable=False,
                    server_default=sa.text("'secondary'"),
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("dashboard_settings"):
        return

    columns = {column["name"] for column in inspector.get_columns("dashboard_settings")}
    if "prefer_earlier_reset_window" in columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.drop_column("prefer_earlier_reset_window")
