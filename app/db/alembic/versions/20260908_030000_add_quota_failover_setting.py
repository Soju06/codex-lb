"""add dashboard quota failover setting"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_030000_add_quota_failover_setting"
down_revision = "20260908_020000_merge_overflow_transport_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("dashboard_settings")}
    if "quota_failover_enabled" not in columns:
        op.add_column(
            "dashboard_settings",
            sa.Column("quota_failover_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("dashboard_settings")}
    if "quota_failover_enabled" in columns:
        op.drop_column("dashboard_settings", "quota_failover_enabled")
