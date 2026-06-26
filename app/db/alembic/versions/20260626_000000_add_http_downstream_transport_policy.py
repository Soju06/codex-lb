"""Add HTTP downstream transport policy settings.

Revision ID: 20260626_000000_add_http_downstream_transport_policy
Revises: 20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads
Create Date: 2026-06-26 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260626_000000_add_http_downstream_transport_policy"
down_revision = "20260611_000000_merge_dashboard_guest_and_weekly_useragent_heads"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("dashboard_settings", "http_downstream_transport_policy"):
        op.add_column(
            "dashboard_settings",
            sa.Column(
                "http_downstream_transport_policy",
                sa.String(),
                server_default=sa.text("'smart'"),
                nullable=False,
            ),
        )
    if not _has_column("api_keys", "transport_policy_override"):
        op.add_column(
            "api_keys",
            sa.Column("transport_policy_override", sa.String(), nullable=True),
        )


def downgrade() -> None:
    if _has_column("api_keys", "transport_policy_override"):
        op.drop_column("api_keys", "transport_policy_override")
    if _has_column("dashboard_settings", "http_downstream_transport_policy"):
        op.drop_column("dashboard_settings", "http_downstream_transport_policy")
