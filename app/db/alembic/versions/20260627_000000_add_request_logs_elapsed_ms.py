"""Add elapsed_ms to request logs.

Revision ID: 20260627_000000_add_request_logs_elapsed_ms
Revises: 20260626_010000_add_request_logs_upstream_transport
Create Date: 2026-06-27 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260627_000000_add_request_logs_elapsed_ms"
down_revision = "20260626_010000_add_request_logs_upstream_transport"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("request_logs", "elapsed_ms"):
        op.add_column("request_logs", sa.Column("elapsed_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    if _has_column("request_logs", "elapsed_ms"):
        op.drop_column("request_logs", "elapsed_ms")
