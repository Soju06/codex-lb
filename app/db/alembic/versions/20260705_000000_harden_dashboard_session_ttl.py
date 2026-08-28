"""harden dashboard session ttl default

Revision ID: 20260705_000000_harden_dashboard_session_ttl
Revises: 20260701_000000_add_weekly_pace_smoothing_minutes
Create Date: 2026-07-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260705_000000_harden_dashboard_session_ttl"
down_revision = "20260701_000000_add_weekly_pace_smoothing_minutes"
branch_labels = None
depends_on = None

OLD_DEFAULT_TTL_SECONDS = 43_200
NEW_DEFAULT_TTL_SECONDS = 31_536_000


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns or "dashboard_session_ttl_seconds" not in columns:
        return

    op.execute(
        sa.text(
            "UPDATE dashboard_settings "
            "SET dashboard_session_ttl_seconds = :new_default "
            "WHERE dashboard_session_ttl_seconds = :old_default"
        ).bindparams(new_default=NEW_DEFAULT_TTL_SECONDS, old_default=OLD_DEFAULT_TTL_SECONDS)
    )
    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.alter_column(
            "dashboard_session_ttl_seconds",
            existing_type=sa.Integer(),
            server_default=sa.text(str(NEW_DEFAULT_TTL_SECONDS)),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns or "dashboard_session_ttl_seconds" not in columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        batch_op.alter_column(
            "dashboard_session_ttl_seconds",
            existing_type=sa.Integer(),
            server_default=sa.text(str(OLD_DEFAULT_TTL_SECONDS)),
        )
