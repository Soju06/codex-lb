"""add requested and actual service tiers to request_logs

Revision ID: 20260320_000000_add_request_log_requested_actual_tiers
Revises: 20260319_183000_normalize_sqlite_account_status_casing
Create Date: 2026-03-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260320_000000_add_request_log_requested_actual_tiers"
down_revision = "20260319_183000_normalize_sqlite_account_status_casing"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "request_logs")
    if not columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        if "requested_service_tier" not in columns:
            batch_op.add_column(sa.Column("requested_service_tier", sa.String(), nullable=True))
        if "actual_service_tier" not in columns:
            batch_op.add_column(sa.Column("actual_service_tier", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "request_logs")
    if not columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        if "actual_service_tier" in columns:
            batch_op.drop_column("actual_service_tier")
        if "requested_service_tier" in columns:
            batch_op.drop_column("requested_service_tier")
