"""Add expiry metadata for quota warmup execution claims."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260806_030000_add_quota_warmup_claim_expiry"
down_revision = "20260820_000000_repair_retired_identity_and_warmup_stamp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("quota_planner_decisions")}
    if "lease_expires_at" not in columns:
        with op.batch_alter_table("quota_planner_decisions") as batch_op:
            batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
    # Claims created before lease metadata existed must not remain permanently
    # active. Expire legacy executing rows so a subsequent scheduler cycle can
    # reclaim them safely.
    #
    # Outside the column guard: the IS NULL predicate already makes this
    # idempotent, and a re-run that finds the column but no backfill (an
    # earlier attempt that added the column and then failed) would otherwise
    # leave those rows unexpirable forever.
    #
    # A fixed past instant, not CURRENT_TIMESTAMP: on PostgreSQL that is a
    # timestamptz rendered into this naive column with the session TimeZone,
    # so a session ahead of UTC would backfill a lease that is still in the
    # future for the UTC-naive comparisons the planner makes.
    op.execute(
        sa.text(
            "UPDATE quota_planner_decisions "
            "SET lease_expires_at = '1970-01-01 00:00:00.000000' "
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
