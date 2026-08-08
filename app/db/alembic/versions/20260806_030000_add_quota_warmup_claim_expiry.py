"""Add expiry metadata for quota warmup execution claims."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260806_030000_add_quota_warmup_claim_expiry"
down_revision = "20260806_020000_add_usage_history_bulk_covering_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("quota_planner_decisions")}
    if "lease_expires_at" not in columns:
        with op.batch_alter_table("quota_planner_decisions") as batch_op:
            batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
        # Claims created before lease metadata existed must not remain
        # permanently active.  Expire legacy executing rows immediately so a
        # subsequent scheduler cycle can reclaim them safely.
        op.execute(
            sa.text(
                "UPDATE quota_planner_decisions "
                "SET lease_expires_at = CURRENT_TIMESTAMP "
                "WHERE action = 'warmup' AND status = 'executing' "
                "AND lease_expires_at IS NULL"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("quota_planner_decisions")}
    if "lease_expires_at" in columns:
        with op.batch_alter_table("quota_planner_decisions") as batch_op:
            batch_op.drop_column("lease_expires_at")
