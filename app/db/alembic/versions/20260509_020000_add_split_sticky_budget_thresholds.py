from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260509_020000_add_split_sticky_budget_thresholds"
down_revision = "20260509_000000_add_api_key_traffic_class"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _columns("dashboard_settings")
    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "sticky_reallocation_primary_budget_threshold_pct" not in columns:
            batch_op.add_column(
                sa.Column(
                    "sticky_reallocation_primary_budget_threshold_pct",
                    sa.Float(),
                    server_default="95.0",
                    nullable=False,
                )
            )
        if "sticky_reallocation_secondary_budget_threshold_pct" not in columns:
            batch_op.add_column(
                sa.Column(
                    "sticky_reallocation_secondary_budget_threshold_pct",
                    sa.Float(),
                    server_default="100.0",
                    nullable=False,
                )
            )

    bind = op.get_bind()
    if "sticky_reallocation_primary_budget_threshold_pct" not in columns:
        bind.execute(
            sa.text(
                "UPDATE dashboard_settings "
                "SET sticky_reallocation_primary_budget_threshold_pct = sticky_reallocation_budget_threshold_pct"
            )
        )


def downgrade() -> None:
    columns = _columns("dashboard_settings")
    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "sticky_reallocation_secondary_budget_threshold_pct" in columns:
            batch_op.drop_column("sticky_reallocation_secondary_budget_threshold_pct")
        if "sticky_reallocation_primary_budget_threshold_pct" in columns:
            batch_op.drop_column("sticky_reallocation_primary_budget_threshold_pct")
