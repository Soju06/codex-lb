"""add dashboard account concurrency caps

Revision ID: 20260711_020000_add_dashboard_account_concurrency_caps
Revises: 20260711_010000_add_account_chatgpt_user_id
Create Date: 2026-07-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260711_020000_add_dashboard_account_concurrency_caps"
down_revision = "20260711_010000_add_account_chatgpt_user_id"
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
        if "proxy_account_response_create_limit" not in dashboard_columns:
            batch_op.add_column(
                sa.Column(
                    "proxy_account_response_create_limit",
                    sa.Integer(),
                    nullable=True,
                )
            )
        if "proxy_account_stream_limit" not in dashboard_columns:
            batch_op.add_column(
                sa.Column(
                    "proxy_account_stream_limit",
                    sa.Integer(),
                    nullable=True,
                )
            )
        if "proxy_account_stream_recovery_reserve" not in dashboard_columns:
            batch_op.add_column(
                sa.Column(
                    "proxy_account_stream_recovery_reserve",
                    sa.Integer(),
                    nullable=True,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    dashboard_columns = _columns(bind, "dashboard_settings")
    if not dashboard_columns:
        return
    with op.batch_alter_table("dashboard_settings") as batch_op:
        for name in (
            "proxy_account_stream_recovery_reserve",
            "proxy_account_stream_limit",
            "proxy_account_response_create_limit",
        ):
            if name in dashboard_columns:
                batch_op.drop_column(name)
