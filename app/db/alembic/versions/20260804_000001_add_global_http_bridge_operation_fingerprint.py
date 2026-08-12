"""fence HTTP bridge operations across durable sessions

Revision ID: 20260804_000001_add_global_http_bridge_operation_fingerprint
Revises: 20260804_000000_add_http_bridge_operations
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260804_000001_add_global_http_bridge_operation_fingerprint"
down_revision = "20260804_000000_add_http_bridge_operations"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_operations"
_FINGERPRINT_INDEX = "uq_http_bridge_operations_request_fingerprint"
_PARENT_INDEX = "idx_http_bridge_operations_parent_state"


def _has_table(connection: Connection) -> bool:
    return sa.inspect(connection).has_table(_TABLE)


def _has_index(connection: Connection, name: str) -> bool:
    return any(index.get("name") == name for index in sa.inspect(connection).get_indexes(_TABLE))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    # A request fingerprint includes the parent response anchor, so it is a
    # global operation identity even when a client reconnects to another
    # durable bridge session. The unique index also closes the race where two
    # workers observe a miss and try to dispatch the same continuation.
    if not _has_index(bind, _FINGERPRINT_INDEX):
        op.create_index(_FINGERPRINT_INDEX, _TABLE, ["request_fingerprint"], unique=True)
    if not _has_index(bind, _PARENT_INDEX):
        op.create_index(_PARENT_INDEX, _TABLE, ["parent_response_id", "state", "updated_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    if _has_index(bind, _PARENT_INDEX):
        op.drop_index(_PARENT_INDEX, table_name=_TABLE)
    if _has_index(bind, _FINGERPRINT_INDEX):
        op.drop_index(_FINGERPRINT_INDEX, table_name=_TABLE)
