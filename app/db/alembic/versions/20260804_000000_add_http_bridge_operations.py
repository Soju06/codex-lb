"""add durable HTTP bridge operation identities and outcomes

Revision ID: 20260804_000000_add_http_bridge_operations
Revises: 20260803_000000_merge_http_bridge_recovery_and_capability_lineage_heads
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260804_000000_add_http_bridge_operations"
down_revision = "20260803_000000_merge_http_bridge_recovery_and_capability_lineage_heads"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_operations"


def _has_table(connection: Connection) -> bool:
    return sa.inspect(connection).has_table(_TABLE)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind):
        return
    op.create_table(
        _TABLE,
        sa.Column("operation_id", sa.String(80), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("parent_response_id", sa.Text(), nullable=True),
        sa.Column("state", sa.String(32), nullable=False, server_default="submitted"),
        sa.Column("response_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["session_id"], ["http_bridge_sessions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "session_id",
            "request_fingerprint",
            name="uq_http_bridge_operations_session_fingerprint",
        ),
    )
    op.create_index(
        "idx_http_bridge_operations_session_parent_state",
        _TABLE,
        ["session_id", "parent_response_id", "state"],
    )
    op.create_index("idx_http_bridge_operations_state_updated", _TABLE, ["state", "updated_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    op.drop_index("idx_http_bridge_operations_state_updated", table_name=_TABLE)
    op.drop_index("idx_http_bridge_operations_session_parent_state", table_name=_TABLE)
    op.drop_table(_TABLE)
