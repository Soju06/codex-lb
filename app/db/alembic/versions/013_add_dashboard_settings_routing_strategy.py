"""add dashboard_settings.routing_strategy

Revision ID: 013_add_dashboard_settings_routing_strategy
Revises: 012_add_import_without_overwrite_and_drop_accounts_email_unique
Create Date: 2026-02-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "013_add_dashboard_settings_routing_strategy"
down_revision = "012_add_import_without_overwrite_and_drop_accounts_email_unique"
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
    if "routing_strategy" in columns:
        return
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "routing_strategy",
                sa.String(),
                nullable=False,
                server_default=sa.text("'usage_weighted'"),
            )
        )


def downgrade() -> None:
    # Downgrade intentionally keeps column to avoid SQLite table rebuild risk.
    return
