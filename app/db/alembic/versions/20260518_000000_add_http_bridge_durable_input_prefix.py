"""add HTTP bridge durable input prefix metadata

Revision ID: 20260518_000000_add_http_bridge_durable_input_prefix
Revises: 20260515_000000_soft_delete_request_logs_on_account_delete
Create Date: 2026-05-18 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260518_000000_add_http_bridge_durable_input_prefix"
down_revision: str | Sequence[str] | None = "20260515_000000_soft_delete_request_logs_on_account_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("http_bridge_sessions", sa.Column("latest_input_item_count", sa.Integer(), nullable=True))
    op.add_column(
        "http_bridge_sessions",
        sa.Column("latest_input_full_fingerprint", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("http_bridge_sessions", "latest_input_full_fingerprint")
    op.drop_column("http_bridge_sessions", "latest_input_item_count")
