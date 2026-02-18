"""seed dashboard_settings singleton row

Revision ID: 005_add_dashboard_settings
Revises: 004_add_accounts_chatgpt_account_id
Create Date: 2026-02-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "005_add_dashboard_settings"
down_revision = "004_add_accounts_chatgpt_account_id"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns:
        return

    existing = bind.execute(
        sa.text("SELECT 1 FROM dashboard_settings WHERE id = :id"),
        {"id": 1},
    ).scalar_one_or_none()
    if existing is not None:
        return

    insert_columns = ["id", "sticky_threads_enabled", "prefer_earlier_reset_accounts"]
    params: dict[str, int | bool] = {
        "id": 1,
        "sticky_threads_enabled": False,
        "prefer_earlier_reset_accounts": False,
    }

    if "import_without_overwrite" in columns:
        insert_columns.append("import_without_overwrite")
        params["import_without_overwrite"] = False

    if "totp_required_on_login" in columns:
        insert_columns.append("totp_required_on_login")
        params["totp_required_on_login"] = False

    if "api_key_auth_enabled" in columns:
        insert_columns.append("api_key_auth_enabled")
        params["api_key_auth_enabled"] = False

    column_list = ", ".join(insert_columns)
    value_list = ", ".join(f":{column}" for column in insert_columns)
    bind.execute(
        sa.text(f"INSERT INTO dashboard_settings ({column_list}) VALUES ({value_list})"),
        params,
    )


def downgrade() -> None:
    # Keep singleton row to avoid destructive rollback behavior.
    return
