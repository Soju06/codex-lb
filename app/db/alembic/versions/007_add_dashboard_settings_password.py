"""add password fields to dashboard_settings

Revision ID: 007_add_dashboard_settings_password
Revises: 006_add_dashboard_settings_totp
Create Date: 2026-02-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "007_add_dashboard_settings_password"
down_revision = "006_add_dashboard_settings_totp"
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

    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "password_hash" not in columns:
            batch_op.add_column(sa.Column("password_hash", sa.Text(), nullable=True))

        if "api_key_auth_enabled" not in columns:
            batch_op.add_column(
                sa.Column(
                    "api_key_auth_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "api_key_auth_enabled" in columns:
            batch_op.drop_column("api_key_auth_enabled")
        if "password_hash" in columns:
            batch_op.drop_column("password_hash")
