"""switch import_without_overwrite default to true

Revision ID: 20260408_000000_switch_import_without_overwrite_default_to_true
Revises: 20260403_000000_add_credit_api_key_limit_values
Create Date: 2026-04-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260408_000000_switch_import_without_overwrite_default_to_true"
down_revision = "20260403_000000_add_credit_api_key_limit_values"
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
    if "import_without_overwrite" not in columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.alter_column(
            "import_without_overwrite",
            existing_type=sa.Boolean(),
            server_default=sa.true(),
        )

    if {"created_at", "updated_at"}.issubset(columns):
        op.execute(
            sa.text(
                """
                UPDATE dashboard_settings
                SET import_without_overwrite = 1
                WHERE import_without_overwrite = 0
                  AND updated_at = created_at
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "dashboard_settings"):
        return
    columns = _columns(bind, "dashboard_settings")
    if "import_without_overwrite" not in columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.alter_column(
            "import_without_overwrite",
            existing_type=sa.Boolean(),
            server_default=sa.false(),
        )
