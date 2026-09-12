"""record where an HTTP bridge operation stands in its terminal append

Revision ID: 20260911_020000_add_http_bridge_terminal_append_phase
Revises: 20260911_010000_merge_pin_index_and_affinity_heads
Create Date: 2026-09-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260911_020000_add_http_bridge_terminal_append_phase"
down_revision = "20260911_010000_merge_pin_index_and_affinity_heads"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_operations"
_COLUMN = "terminal_append_phase"


def _has_table(connection: Connection) -> bool:
    return sa.inspect(connection).has_table(_TABLE)


def _has_column(connection: Connection) -> bool:
    return any(item["name"] == _COLUMN for item in sa.inspect(connection).get_columns(_TABLE))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind) or _has_column(bind):
        return
    # ``pending`` for every existing row is the correct backfill. The column
    # fences the terminal transcript write of one dispatch, and no row written
    # before this revision carries such a fence: a legacy terminal append
    # committed ``event_spool_complete = true`` in the same transaction, and
    # the legacy fallback settlement left no distinguishing mark. Backfilling
    # anything stricter would refuse the terminal append of operations that are
    # in flight across the upgrade, which is precisely the fail-closed shape
    # this column exists to avoid: an ordinary operation is already
    # ``state = completed`` with an incomplete spool while its terminal append
    # runs, because the relay publishes the operation state first.
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.String(length=16), nullable=False, server_default=sa.text("'pending'")),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind) and _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
