"""add dashboard_settings global model force fields

Revision ID: 015_add_dashboard_settings_global_model_force
Revises: 014_add_model_overrides_and_request_actor_fields
Create Date: 2026-02-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "015_add_dashboard_settings_global_model_force"
down_revision = "014_add_model_overrides_and_request_actor_fields"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "global_model_force_enabled" not in columns:
            batch_op.add_column(sa.Column("global_model_force_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")))
        if "global_model_force_model" not in columns:
            batch_op.add_column(sa.Column("global_model_force_model", sa.String(), nullable=True))
        if "global_model_force_reasoning_effort" not in columns:
            batch_op.add_column(sa.Column("global_model_force_reasoning_effort", sa.String(), nullable=True))


def downgrade() -> None:
    # Intentionally no-op to avoid destructive table rebuilds on SQLite.
    return
