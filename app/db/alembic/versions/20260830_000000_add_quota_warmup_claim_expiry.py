"""Add expiry metadata for quota warmup execution claims.

Revision ID: 20260830_000000_add_quota_warmup_claim_expiry
Revises: 20260828_000000_add_accounts_chatgpt_identity_index
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260830_000000_add_quota_warmup_claim_expiry"
down_revision = "20260828_000000_add_accounts_chatgpt_identity_index"
branch_labels = None
depends_on = None


# Mirrors the runtime lease floor: _warmup_claim_ttl_seconds() floors the
# claim TTL at http_responses_stream_request_budget_seconds (default 7200) so
# a healthy probe provably outlives its lease. Legacy probes ran under the
# same default stream budget, so a lease window of this length anchored to
# the claim timestamp outlives any legacy probe that can still be in flight.
_LEGACY_CLAIM_LEASE_WINDOW_SECONDS = 7200


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("quota_planner_decisions")}
    if "lease_expires_at" not in columns:
        with op.batch_alter_table("quota_planner_decisions") as batch_op:
            batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
    # Claims created before lease metadata existed must not remain permanently
    # active, but they must not be expired immediately either: a pre-migration
    # worker can still be mid-probe when this migration runs (rolling deploys
    # keep the old process serving), and an instantly-expired claim would let
    # the new scheduler reclaim it and send a duplicate probe while the first
    # is still in flight — the success log is only written after the probe
    # returns, so reconciliation cannot see it yet. Backfill a lease anchored
    # to the claim timestamp instead: ``executed_at`` (the claim stamp),
    # falling back to ``created_at`` for rows claimed by versions that predate
    # claim stamping. Rows whose claim is older than the legacy probe window
    # expire immediately; a live legacy probe keeps a conservative execution
    # window and is swept once it can no longer be in flight.
    #
    # Outside the column guard: the IS NULL predicate already makes this
    # idempotent, and a re-run that finds the column but no backfill (an
    # earlier attempt that added the column and then failed) would otherwise
    # leave those rows unexpirable forever.
    #
    # Derived from the stored naive-UTC column values, not CURRENT_TIMESTAMP:
    # on PostgreSQL that is a timestamptz rendered into this naive column with
    # the session TimeZone, so a session ahead of UTC would skew the backfill
    # relative to the UTC-naive comparisons the planner makes.
    if bind.dialect.name == "postgresql":
        lease_expr = (
            "COALESCE(executed_at, created_at, TIMESTAMP '1970-01-01 00:00:00') "
            f"+ make_interval(secs => {_LEGACY_CLAIM_LEASE_WINDOW_SECONDS})"
        )
    else:
        lease_expr = (
            "(strftime('%Y-%m-%d %H:%M:%f', "
            "COALESCE(executed_at, created_at, '1970-01-01 00:00:00'), "
            f"'+{_LEGACY_CLAIM_LEASE_WINDOW_SECONDS} seconds') || '000')"
        )
    op.execute(
        sa.text(
            "UPDATE quota_planner_decisions "
            f"SET lease_expires_at = {lease_expr} "
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
