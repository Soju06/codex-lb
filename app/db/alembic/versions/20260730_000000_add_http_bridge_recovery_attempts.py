"""add durable HTTP bridge recovery attempts

Revision ID: 20260730_000000_add_http_bridge_recovery_attempts
Revises: 20260728_000000_merge_pending_tool_calls_and_rollup_repair_heads
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260730_000000_add_http_bridge_recovery_attempts"
down_revision = "20260728_000000_merge_pending_tool_calls_and_rollup_repair_heads"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_recovery_attempts"
_STATE = sa.Enum("unknown", "replayed", name="http_bridge_recovery_attempt_state")


def _has_table(connection: Connection) -> bool:
    return sa.inspect(connection).has_table(_TABLE)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(255), nullable=False),
        sa.Column("account_id", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("replay_safe", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("state", _STATE, nullable=False, server_default="unknown"),
        sa.Column("response_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["session_id"], ["http_bridge_sessions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "session_id",
            "request_fingerprint",
            name="uq_http_bridge_recovery_attempts_session_fingerprint",
        ),
    )
    op.create_index("idx_http_bridge_recovery_attempts_state", _TABLE, ["state", "updated_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    op.drop_index("idx_http_bridge_recovery_attempts_state", table_name=_TABLE)
    op.drop_table(_TABLE)
    _STATE.drop(bind, checkfirst=True)
