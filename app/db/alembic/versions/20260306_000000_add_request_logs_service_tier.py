"""add service_tier to request_logs

Revision ID: 20260306_000000_add_request_logs_service_tier
Revises: 20260228_030000_add_api_firewall_allowlist
Create Date: 2026-03-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260306_000000_add_request_logs_service_tier"
down_revision = "20260228_030000_add_api_firewall_allowlist"
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
    if not columns or "service_tier" in columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.add_column(sa.Column("service_tier", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "request_logs")
    if "service_tier" not in columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.drop_column("service_tier")
