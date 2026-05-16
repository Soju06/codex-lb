from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260515_050000_live_schema_compat"
down_revision = "20260515_040000_merge_security_work_stack_heads"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    account_columns = _columns("accounts")
    if "routing_policy" not in account_columns:
        with op.batch_alter_table("accounts") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "routing_policy",
                    sa.String(),
                    server_default="normal",
                    nullable=False,
                )
            )

    request_log_columns = _columns("request_logs")
    if "slim_summary_json" not in request_log_columns:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.add_column(sa.Column("slim_summary_json", sa.Text(), nullable=True))

    dashboard_columns = _columns("dashboard_settings")
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.alter_column(
            "sticky_reallocation_primary_budget_threshold_pct",
            existing_type=sa.Float(),
            type_=sa.REAL(),
            existing_nullable=False,
            existing_server_default=sa.text("95.0"),
        )
        batch_op.alter_column(
            "sticky_reallocation_secondary_budget_threshold_pct",
            existing_type=sa.Float(),
            type_=sa.REAL(),
            existing_nullable=False,
            existing_server_default=sa.text("100.0"),
        )
        if "additional_quota_routing_policies_json" not in dashboard_columns:
            batch_op.add_column(
                sa.Column(
                    "additional_quota_routing_policies_json",
                    sa.Text(),
                    server_default="{}",
                    nullable=False,
                )
            )


def downgrade() -> None:
    dashboard_columns = _columns("dashboard_settings")
    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "additional_quota_routing_policies_json" in dashboard_columns:
            batch_op.drop_column("additional_quota_routing_policies_json")
        batch_op.alter_column(
            "sticky_reallocation_secondary_budget_threshold_pct",
            existing_type=sa.REAL(),
            type_=sa.Float(),
            existing_nullable=False,
            existing_server_default=sa.text("100.0"),
        )
        batch_op.alter_column(
            "sticky_reallocation_primary_budget_threshold_pct",
            existing_type=sa.REAL(),
            type_=sa.Float(),
            existing_nullable=False,
            existing_server_default=sa.text("95.0"),
        )

    request_log_columns = _columns("request_logs")
    if "slim_summary_json" in request_log_columns:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.drop_column("slim_summary_json")

    account_columns = _columns("accounts")
    if "routing_policy" in account_columns:
        with op.batch_alter_table("accounts") as batch_op:
            batch_op.drop_column("routing_policy")
