"""add anonymous telemetry identity and consent

Revision ID: 20260806_000000_add_anonymous_telemetry
Revises: 20260812_000000_merge_recovery_dispatch_and_hourly_cancelled_heads
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260806_000000_add_anonymous_telemetry"
down_revision = "20260812_000000_merge_recovery_dispatch_and_hourly_cancelled_heads"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("dashboard_settings")}


def upgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "telemetry_consent" not in columns:
            batch_op.add_column(
                sa.Column(
                    "telemetry_consent",
                    sa.String(length=16),
                    server_default=sa.text("'undecided'"),
                    nullable=False,
                )
            )
        if "telemetry_instance_id" not in columns:
            batch_op.add_column(sa.Column("telemetry_instance_id", sa.String(length=36), nullable=True))
        if "telemetry_private_key_encrypted" not in columns:
            batch_op.add_column(sa.Column("telemetry_private_key_encrypted", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "telemetry_private_key_encrypted" in columns:
            batch_op.drop_column("telemetry_private_key_encrypted")
        if "telemetry_instance_id" in columns:
            batch_op.drop_column("telemetry_instance_id")
        if "telemetry_consent" in columns:
            batch_op.drop_column("telemetry_consent")
