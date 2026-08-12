"""add durable HTTP bridge operation request and event spool

Revision ID: 20260805_000000_add_http_bridge_operation_spool
Revises: 20260804_000001_add_global_http_bridge_operation_fingerprint
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260805_000000_add_http_bridge_operation_spool"
down_revision = "20260804_000001_add_global_http_bridge_operation_fingerprint"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_operations"
_EVENTS_TABLE = "http_bridge_operation_events"


def _has_table(connection: Connection, table: str) -> bool:
    return sa.inspect(connection).has_table(table)


def _has_column(connection: Connection, table: str, column: str) -> bool:
    return any(item["name"] == column for item in sa.inspect(connection).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _TABLE):
        if not _has_column(bind, _TABLE, "request_text"):
            op.add_column(_TABLE, sa.Column("request_text", sa.Text(), nullable=True))
        if not _has_column(bind, _TABLE, "event_bytes"):
            op.add_column(_TABLE, sa.Column("event_bytes", sa.Integer(), nullable=False, server_default="0"))
        if not _has_column(bind, _TABLE, "event_spool_complete"):
            op.add_column(
                _TABLE,
                sa.Column("event_spool_complete", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            )
    if _has_table(bind, _EVENTS_TABLE):
        return
    op.create_table(
        _EVENTS_TABLE,
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("operation_id", sa.String(80), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_fingerprint", sa.String(64), nullable=False),
        sa.Column("event_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["operation_id"], [f"{_TABLE}.operation_id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "operation_id",
            "event_fingerprint",
            name="uq_http_bridge_operation_events_operation_fingerprint",
        ),
    )
    op.create_index(
        "idx_http_bridge_operation_events_operation_sequence",
        _EVENTS_TABLE,
        ["operation_id", "sequence_number"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _EVENTS_TABLE):
        op.drop_index("idx_http_bridge_operation_events_operation_sequence", table_name=_EVENTS_TABLE)
        op.drop_table(_EVENTS_TABLE)
    if _has_table(bind, _TABLE):
        for column in ("event_spool_complete", "event_bytes", "request_text"):
            if _has_column(bind, _TABLE, column):
                op.drop_column(_TABLE, column)
