"""backfill pristine dashboard settings defaults for staged fresh installs

Revision ID: 20260410_000000_backfill_pristine_dashboard_settings_defaults
Revises: 20260409_010000_add_dashboard_settings_bootstrap_token
Create Date: 2026-04-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260410_000000_backfill_pristine_dashboard_settings_defaults"
down_revision = "20260409_010000_add_dashboard_settings_bootstrap_token"
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
    required_columns = {
        "sticky_threads_enabled",
        "prefer_earlier_reset_accounts",
        "password_hash",
        "totp_secret_encrypted",
        "totp_required_on_login",
        "api_key_auth_enabled",
        "created_at",
        "updated_at",
    }
    if not required_columns.issubset(columns):
        return

    extra_empty_tables = [
        table for table in ("accounts", "api_keys", "request_logs", "audit_logs") if _table_exists(bind, table)
    ]
    empty_checks = "\n".join(f"  AND NOT EXISTS (SELECT 1 FROM {table} LIMIT 1)" for table in extra_empty_tables)

    op.execute(
        sa.text(
            f"""
            UPDATE dashboard_settings
            SET sticky_threads_enabled = TRUE,
                prefer_earlier_reset_accounts = TRUE
            WHERE id = 1
              AND sticky_threads_enabled = FALSE
              AND prefer_earlier_reset_accounts = FALSE
              AND password_hash IS NULL
              AND totp_secret_encrypted IS NULL
              AND totp_required_on_login = FALSE
              AND api_key_auth_enabled = FALSE
              AND created_at = updated_at
{empty_checks}
            """
        )
    )


def downgrade() -> None:
    return
