"""add cancelled_count measure to hourly usage rollups

Additive DDL only: the new measure is populated by the hourly fold pass going
forward (sum(status = 'cancelled')), and error_count narrows to
sum(status NOT IN ('success', 'cancelled')) at the same deploy. Rows folded
before this revision keep the legacy sum(status != 'success') error fold and
read cancelled_count = 0 via the server default — they are deliberately NOT
backfilled (raw rows below the watermark may already be retention-pruned, so
the old fold cannot be re-split), which shows as a disclosed step change on
error-rate trends (#1552).

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


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind, _TABLE):
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(
            sa.Column(
                _COLUMN,
                sa.BigInteger(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind, _TABLE):
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column(_COLUMN)
