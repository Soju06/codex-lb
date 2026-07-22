"""Reconcile schemas that ran the earlier security-work stack.

Revision ID: 20260711_010000_reconcile_security_lineage_schema
Revises: 20260710_010000_add_http_bridge_security_lineage
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260711_010000_reconcile_security_lineage_schema"
down_revision = "20260710_010000_add_http_bridge_security_lineage"
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
    if dashboard_columns and "prohibit_fast_mode" not in dashboard_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(
                sa.Column("prohibit_fast_mode", sa.Boolean(), nullable=False, server_default=sa.false())
            )

    quota_columns = _columns(bind, "quota_planner_settings")
    if quota_columns:
        with op.batch_alter_table("quota_planner_settings") as batch_op:
            if "auto_redeem_expiring_reset_credits" not in quota_columns:
                batch_op.add_column(
                    sa.Column(
                        "auto_redeem_expiring_reset_credits",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.false(),
                    )
                )
            if "reset_credit_redeem_lead_minutes" not in quota_columns:
                batch_op.add_column(
                    sa.Column(
                        "reset_credit_redeem_lead_minutes",
                        sa.Integer(),
                        nullable=False,
                        server_default="30",
                    )
                )

    bridge_columns = _columns(bind, "http_bridge_sessions")
    if bridge_columns:
        with op.batch_alter_table("http_bridge_sessions") as batch_op:
            if "latest_pending_function_call_ids" not in bridge_columns:
                batch_op.add_column(sa.Column("latest_pending_function_call_ids", sa.Text(), nullable=True))
            if "latest_pending_custom_tool_call_ids" not in bridge_columns:
                batch_op.add_column(sa.Column("latest_pending_custom_tool_call_ids", sa.Text(), nullable=True))


def downgrade() -> None:
    # This revision reconciles columns that may already have been created by a
    # previously deployed aggregate. It cannot prove column ownership during a
    # downgrade, so removing them could destroy pre-existing live data. Their
    # feature-owning revisions may remove them when downgrading farther.
    return
