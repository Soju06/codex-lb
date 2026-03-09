"""add dashboard_settings.http_proxy_url

Revision ID: 20260309_000000_add_dashboard_settings_http_proxy_url
Revises: 20260228_030000_add_api_firewall_allowlist
Create Date: 2026-03-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260309_000000_add_dashboard_settings_http_proxy_url"
down_revision = "20260228_030000_add_api_firewall_allowlist"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "dashboard_settings"):
        return
    columns = _columns(bind, "dashboard_settings")
    if "http_proxy_url" in columns:
        return
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.add_column(sa.Column("http_proxy_url", sa.Text(), nullable=True))


def downgrade() -> None:
    return
