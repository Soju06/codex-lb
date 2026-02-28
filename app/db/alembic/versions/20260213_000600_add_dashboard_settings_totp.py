"""add totp fields to dashboard_settings

Revision ID: 20260213_000600_add_dashboard_settings_totp
Revises: 20260213_000500_add_dashboard_settings
Create Date: 2026-02-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260213_000600_add_dashboard_settings_totp"
down_revision = "20260213_000500_add_dashboard_settings"
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
        if "totp_required_on_login" not in columns:
            batch_op.add_column(
                sa.Column(
                    "totp_required_on_login",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )

        if "totp_secret_encrypted" not in columns:
            batch_op.add_column(sa.Column("totp_secret_encrypted", sa.LargeBinary(), nullable=True))

        if "totp_last_verified_step" not in columns:
            batch_op.add_column(sa.Column("totp_last_verified_step", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "totp_last_verified_step" in columns:
            batch_op.drop_column("totp_last_verified_step")
        if "totp_secret_encrypted" in columns:
            batch_op.drop_column("totp_secret_encrypted")
        if "totp_required_on_login" in columns:
            batch_op.drop_column("totp_required_on_login")
