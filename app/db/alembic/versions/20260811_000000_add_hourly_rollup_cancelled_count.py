"""add cancelled_count measure to hourly usage rollups

Additive DDL plus one marker stamp: the new measure is populated by the
hourly fold pass going forward (sum(status = 'cancelled')), and error_count
narrows to sum(status NOT IN ('success', 'cancelled')) at the same deploy.
Rows folded before this revision keep the legacy sum(status != 'success')
error fold and read cancelled_count = 0 via the server default — they are
deliberately NOT backfilled (raw rows below the watermark may already be
retention-pruned, so the old fold cannot be re-split), which shows as a
disclosed step change on error-rate trends (#1552).

Rolling-upgrade fence: this migration runs before old replicas drain, so a
legacy leader can keep folding (and advancing the shared watermark) with the
old error fold afterwards. ``account_usage_rollup_state.upgrade_repair_from``
records where that legacy-suspect range starts: existing state rows are
stamped with their current ``hourly_folded_through``; the epoch server
default covers a state row bootstrapped by an OLD replica after this
migration (its whole backfill is legacy-folded). New code refolds
``[upgrade_repair_from, hourly_folded_through)`` from raw on its first fold
pass and then sets the marker to NULL — a value only new code ever writes.

Revision ID: 20260811_000000_add_hourly_rollup_cancelled_count
Revises: 20260806_120000_add_http_bridge_owner_process_epoch
Create Date: 2026-08-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260811_000000_add_hourly_rollup_cancelled_count"
down_revision = "20260806_120000_add_http_bridge_owner_process_epoch"
branch_labels = None
depends_on = None

_TABLE = "request_usage_hourly_rollups"
_COLUMN = "cancelled_count"
_STATE_TABLE = "account_usage_rollup_state"
_MARKER_COLUMN = "upgrade_repair_from"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()

    if _COLUMN not in _columns(bind, _TABLE):
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.add_column(
                sa.Column(
                    _COLUMN,
                    sa.BigInteger(),
                    server_default=sa.text("0"),
                    nullable=False,
                )
            )

    state_columns = _columns(bind, _STATE_TABLE)
    if state_columns and _MARKER_COLUMN not in state_columns:
        with op.batch_alter_table(_STATE_TABLE) as batch_op:
            batch_op.add_column(
                sa.Column(
                    _MARKER_COLUMN,
                    sa.DateTime(),
                    server_default=sa.text("'1970-01-01 00:00:00'"),
                    nullable=True,
                )
            )
        # Existing rows: the legacy-suspect range starts at the watermark as
        # of this migration — everything a legacy leader folds afterwards
        # lies above it. Rows inserted later by old code keep the epoch
        # server default (their entire backfill is legacy-suspect).
        op.execute(sa.text(f"UPDATE {_STATE_TABLE} SET {_MARKER_COLUMN} = hourly_folded_through"))


def downgrade() -> None:
    bind = op.get_bind()

    if _MARKER_COLUMN in _columns(bind, _STATE_TABLE):
        with op.batch_alter_table(_STATE_TABLE) as batch_op:
            batch_op.drop_column(_MARKER_COLUMN)

    if _COLUMN in _columns(bind, _TABLE):
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
