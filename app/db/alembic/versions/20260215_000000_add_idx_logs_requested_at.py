"""add index on request_logs.requested_at

Revision ID: 20260215_000000_add_idx_logs_requested_at
Revises: 20260214_000000_add_api_key_limits
Create Date: 2026-02-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260215_000000_add_idx_logs_requested_at"
down_revision = "20260214_000000_add_api_key_limits"
branch_labels = None
depends_on = None


def _index_exists(connection: Connection, index_name: str, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return False
    indexes = inspector.get_indexes(table_name)
    return any(idx["name"] == index_name for idx in indexes)


def upgrade() -> None:
    bind = op.get_bind()
    if not _index_exists(bind, "idx_logs_requested_at", "request_logs"):
        op.create_index("idx_logs_requested_at", "request_logs", ["requested_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if _index_exists(bind, "idx_logs_requested_at", "request_logs"):
        op.drop_index("idx_logs_requested_at", table_name="request_logs")
