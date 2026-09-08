"""Drop the retired prewarm canary request-log columns.

Revision ID: 20260908_000000_drop_prewarm_canary_columns
Revises: 20260830_000000_add_quota_warmup_claim_expiry
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_000000_drop_prewarm_canary_columns"
down_revision = "20260830_000000_add_quota_warmup_claim_expiry"
branch_labels = None
depends_on = None

# The prewarm canary experiment retired in reduce-settings-surface-phase-4
# (issue #1340 phase 4, v1.22.0). The columns stayed declared but unwritten
# for the rolling-upgrade window so pre-phase-4 replicas could keep inserting
# request logs while the Helm pre-upgrade migration job ran ahead of the
# workload roll. Every release since (1.22 -> 1.24) has shipped without a
# writer, so the drop is now safe.
_RETIRED_COLUMNS = ("prewarm_canary_bucket", "prewarm_eligible_reason")


def _request_log_columns(bind: sa.engine.Connection) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table("request_logs"):
        return set()
    return {str(column["name"]) for column in inspector.get_columns("request_logs")}


def upgrade() -> None:
    existing = _request_log_columns(op.get_bind())
    to_drop = [name for name in _RETIRED_COLUMNS if name in existing]
    if not to_drop:
        return
    # batch_alter_table recreates the table on SQLite (no native DROP COLUMN
    # for older builds) and degrades to plain ALTER TABLE on PostgreSQL.
    with op.batch_alter_table("request_logs") as batch_op:
        for column_name in to_drop:
            batch_op.drop_column(column_name)


def downgrade() -> None:
    existing = _request_log_columns(op.get_bind())
    to_add = [name for name in _RETIRED_COLUMNS if name not in existing]
    if not to_add:
        return
    with op.batch_alter_table("request_logs") as batch_op:
        for column_name in to_add:
            batch_op.add_column(sa.Column(column_name, sa.String(), nullable=True))
